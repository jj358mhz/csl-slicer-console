"""Slicer Updates (#71): change version, configuration, encoding profile and plugin.

Every step re-reads the slicer from Uplynk, because the stored copy is only
captured once per run and an update replaces the whole configuration.

Network work runs in threads against plain data; only the calling thread
touches the database. Gunicorn kills a request after 30s, so slicers that
haven't started by NETWORK_BUDGET_SECONDS are reported rather than attempted.
"""

from __future__ import annotations

import difflib
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from flask import current_app, url_for
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.crypto import CryptoError, decrypt, encrypt
from app.models import CONFIG_SOURCE_UPDATE, AuditEvent, Slicer, UplynkAccount, User, db
from app.uplynk.discovery import UplynkAPIError, UplynkDiscoveryClient
from app.uplynk.slicer_config import (
    SlicerConfig,
    apply_line_edits,
    apply_overrides,
    line_values,
    normalize_configuration,
)
from app.uplynk.states import is_running_state
from app.uplynk.target_state import patch_slicer
from app.uplynk.update_status import (
    WRITE_SCOPE,
    StatusExplanation,
    explain_no_response,
    explain_status,
    has_write_scope,
)

AUDIT_METHOD = "update_settings"
MAX_WORKERS = 8
NETWORK_BUDGET_SECONDS = 12
FETCH_TIMEOUT_SECONDS = 6
PATCH_TIMEOUT_SECONDS = 8

STATUS_UPDATED = "updated"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_NOT_SENT = "not_sent"

OUT_OF_TIME = "Not reached in time. Nothing was sent to this slicer."


def _posted_overrides(form) -> tuple[tuple[str, str], ...]:
    """Ticked Override rows."""
    overrides = []
    for name in form:
        if name.startswith("override_on:"):
            key = name.removeprefix("override_on:")
            value = form.get(f"override_value:{key}", "")
            overrides.append((key, " ".join(value.split("\n")).strip()))
    return tuple(sorted(overrides))


@dataclass(frozen=True)
class UpdateRequest:
    """What the admin asked to change. Empty fields mean no change."""

    slicer_version: str = ""
    encoding_profile_id: str = ""
    configuration: str = ""
    config_base: str = ""
    plugin_id: str = ""
    plugin_version: str = ""
    batch: bool = False
    # Settings that differ between slicers, unlocked to one value: (key, value).
    overrides: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_form(cls, form) -> UpdateRequest:
        plugin_id, _, plugin_version = form.get("plugin", "").partition("::")
        wants_plugin = form.get("update_plugin") and plugin_id and plugin_version
        return cls(
            slicer_version=form.get("slicer_version", "").strip(),
            encoding_profile_id=form.get("encoding_profile_id", "").strip(),
            configuration=normalize_configuration(form.get("configuration")),
            config_base=normalize_configuration(form.get("config_base")),
            plugin_id=plugin_id if wants_plugin else "",
            plugin_version=plugin_version if wants_plugin else "",
            batch=bool(form.get("batch")),
            overrides=_posted_overrides(form),
        )

    @property
    def config_edited(self) -> bool:
        return self.configuration != self.config_base

    @property
    def override_map(self) -> dict[str, str]:
        return dict(self.overrides)

    @property
    def blank_overrides(self) -> list[str]:
        return [key for key, value in self.overrides if not value]

    @property
    def plugin_value(self) -> str:
        return f"{self.plugin_id}::{self.plugin_version}" if self.plugin_id else ""


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: str
    after: str


@dataclass(frozen=True)
class DiffLine:
    removed: bool
    text: str


@dataclass
class SlicerPlan:
    """What an update would do to one slicer, from its current settings."""

    pk: int
    slicer_id: str
    account_label: str
    state: str | None = None
    error: str | None = None
    fields: dict = field(default_factory=dict)
    changes: list[FieldChange] = field(default_factory=list)
    diff: list[DiffLine] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return is_running_state(self.state)

    @property
    def skipped(self) -> bool:
        return not self.error and not self.fields

    @property
    def restarts(self) -> bool:
        return self.running and bool(self.fields)


@dataclass
class UpdateResult:
    """One slicer's outcome, for the results page."""

    plan: SlicerPlan
    status: str
    explanation: StatusExplanation | None = None
    status_code: int | None = None
    body: dict | str | None = None

    @property
    def retryable(self) -> bool:
        return self.status in (STATUS_FAILED, STATUS_NOT_SENT)


@dataclass(frozen=True)
class SettingDelta:
    """A setting whose value differs between the selected slicers."""

    key: str
    slicer_count: int
    total: int
    by_value: dict[str, list[str]]


@dataclass(frozen=True)
class EditOptions:
    """Choices for the edit form, available to every selected slicer."""

    versions: list[str]
    plugins: dict[str, list[str]]
    warnings: list[str]


@dataclass
class _Target:
    """Everything a worker thread needs, free of database objects."""

    pk: int
    slicer_id: str
    account_label: str
    region: str | None
    problem: str | None = None
    client: UplynkDiscoveryClient | None = None
    key: dict = field(default_factory=dict)


def account_write_problem(account: UplynkAccount) -> str | None:
    """Why this account's saved key can't be used for updates, if it can't."""
    if not account.has_scoped_key:
        return f"{account.label} has no scoped API key. Upload one on the Uplynk Accounts page."
    if not has_write_scope(account.scoped_scp):
        return (
            f"The key saved for {account.label} doesn't list {WRITE_SCOPE}. Upload a key "
            "with that scope on the Uplynk Accounts page."
        )
    return None


def syncable_accounts() -> list[dict[str, str]]:
    """Accounts Sync all can reach, as the progress panel wants them."""
    accounts = db.session.scalars(select(UplynkAccount).order_by(UplynkAccount.label))
    return [
        {"label": a.label, "url": url_for("admin.sync_account_route", account_id=a.id)}
        for a in accounts
        if a.has_scoped_key and not a.is_paused
    ]


def list_updatable_slicers() -> list[Slicer]:
    """Every active slicer on an unpaused account, grouped by account."""
    return list(
        db.session.scalars(
            select(Slicer)
            .join(Slicer.uplynk_account)
            .options(joinedload(Slicer.uplynk_account))
            .where(Slicer.is_active.is_(True), UplynkAccount.is_paused.is_(False))
            .order_by(UplynkAccount.label, Slicer.slicer_id)
        )
    )


def selected_slicers(pks: Iterable[int]) -> list[Slicer]:
    wanted = set(pks)
    return [s for s in list_updatable_slicers() if s.id in wanted]


def _targets(slicers: list[Slicer]) -> list[_Target]:
    api_base = current_app.config["UPLYNK_API_BASE"]
    keys: dict[int, dict | str] = {}
    targets = []
    for slicer in slicers:
        account = slicer.uplynk_account
        if account.id not in keys:
            problem = account_write_problem(account)
            if problem is None:
                try:
                    keys[account.id] = {
                        "kid": account.scoped_kid,
                        "sub": account.scoped_sub,
                        "private_b64": decrypt(account.scoped_private_b64_encrypted),
                        "scp": account.scoped_scp,
                    }
                except CryptoError as e:
                    keys[account.id] = f"Failed to decrypt the key for {account.label}: {e}"
            else:
                keys[account.id] = problem
        key = keys[account.id]
        target = _Target(slicer.id, slicer.slicer_id, account.label, slicer.region)
        if isinstance(key, str):
            target.problem = key
        else:
            target.key = key
            target.client = UplynkDiscoveryClient(
                api_base=api_base, timeout=FETCH_TIMEOUT_SECONDS, **key
            )
        targets.append(target)
    return targets


def _run_all(items: list, work: Callable, out_of_time: Callable) -> list:
    """Run `work` over items in parallel, keeping order, within the time budget."""
    deadline = time.monotonic() + NETWORK_BUDGET_SECONDS

    def run(item):
        return out_of_time(item) if time.monotonic() > deadline else work(item)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return list(pool.map(run, items))


def fetch_current_listing(
    slicers: list[Slicer],
) -> tuple[dict[int, tuple[str | None, SlicerConfig]], list[str]]:
    """Each slicer's live state and settings by pk, with one list call per account.

    Stored copies only exist for slicers captured while running, and states go
    stale between polls. Also returns which accounts couldn't be read. Accounts
    with a read-only key can still be listed; only updates need write.
    """
    api_base = current_app.config["UPLYNK_API_BASE"]
    accounts = {s.uplynk_account_id: s.uplynk_account for s in slicers}
    warnings = []
    clients = []
    for account in accounts.values():
        if not account.has_scoped_key:
            warnings.append(account.label)
            continue
        try:
            private_b64 = decrypt(account.scoped_private_b64_encrypted)
        except CryptoError:
            warnings.append(account.label)
            continue
        clients.append(
            (
                account,
                UplynkDiscoveryClient(
                    api_base=api_base,
                    kid=account.scoped_kid,
                    sub=account.scoped_sub,
                    private_b64=private_b64,
                    scp=account.scoped_scp,
                    timeout=FETCH_TIMEOUT_SECONDS,
                ),
            )
        )
    labels = {account.id: account.label for account, _ in clients}
    logger = current_app.logger  # Worker threads have no app context.

    def fetch(entry):
        account_id, client = entry
        try:
            return account_id, client.list_slicer_snapshots()
        except UplynkAPIError as e:
            logger.warning("Listing %s failed: %s", labels[account_id], e)
            return account_id, None

    results = _run_all(
        [(account.id, client) for account, client in clients],
        fetch,
        lambda entry: (entry[0], None),
    )
    by_account = dict(results)
    warnings += [labels[account_id] for account_id, snapshots in results if snapshots is None]

    current = {}
    for slicer in slicers:
        snapshot = (by_account.get(slicer.uplynk_account_id) or {}).get(slicer.slicer_id)
        if snapshot:
            current[slicer.id] = snapshot
    return current, sorted(warnings)


def build_plan(target: _Target, state: str | None, config: SlicerConfig, update: UpdateRequest):
    """Compare the request with a slicer's current settings."""
    plan = SlicerPlan(target.pk, target.slicer_id, target.account_label, state=state)
    version = update.slicer_version
    if version and version != config.slicer_version:
        plan.fields["slicer_version"] = version
        plan.changes.append(FieldChange("slicer_version", config.slicer_version or "—", version))
    profile = update.encoding_profile_id
    if profile and profile != config.encoding_profile_id:
        plan.fields["encoding_profile_id"] = profile
        plan.changes.append(
            FieldChange("encoding_profile_id", config.encoding_profile_id or "—", profile)
        )
    if update.plugin_id and (
        update.plugin_id != config.plugin_id or update.plugin_version != config.plugin_version
    ):
        plan.fields["plugin"] = {"id": update.plugin_id, "version": update.plugin_version}
        before = f"{config.plugin_id} v{config.plugin_version}" if config.plugin_id else "none"
        plan.changes.append(
            FieldChange("plugin", before, f"{update.plugin_id} v{update.plugin_version}")
        )
    if update.config_edited or update.overrides:
        current = normalize_configuration(config.configuration)
        wanted = update.configuration
        if update.batch:
            # Only what was edited: everything else on this slicer stays its own.
            wanted = apply_line_edits(config.configuration, update.config_base, wanted)
            wanted = apply_overrides(wanted, update.override_map)
        if wanted != current:
            plan.fields["configuration"] = f"{wanted}\n"
            plan.diff = _line_diff(current, wanted)
    return plan


def _line_diff(before: str, after: str) -> list[DiffLine]:
    lines = difflib.unified_diff(before.split("\n"), after.split("\n"), lineterm="", n=0)
    return [
        DiffLine(line[0] == "-", line[1:])
        for line in lines
        if line[:1] in ("-", "+") and not line.startswith(("---", "+++"))
    ]


def differing_settings(
    configs: list[tuple[str, SlicerConfig]], keys: list[str]
) -> list[SettingDelta]:
    """What each slicer has for the settings that differ, for the locked rows."""
    values = [(slicer_id, line_values(config.configuration)) for slicer_id, config in configs]
    deltas = []
    for key in keys:
        by_value: dict[str, list[str]] = {}
        for slicer_id, settings in values:
            if key in settings:
                by_value.setdefault(settings[key], []).append(slicer_id)
        deltas.append(
            SettingDelta(key, sum(len(ids) for ids in by_value.values()), len(values), by_value)
        )
    return deltas


def fetch_snapshots(slicers: list[Slicer]) -> list[tuple[_Target, SlicerConfig | None, str | None]]:
    """Each slicer's current configuration from Uplynk, or why it couldn't be read."""

    def fetch(target: _Target):
        if target.problem:
            return target, None, target.problem
        try:
            return target, target.client.retrieve_slicer_snapshot(target.slicer_id)[1], None
        except UplynkAPIError as e:
            return target, None, str(e)

    return _run_all(_targets(slicers), fetch, lambda t: (t, None, OUT_OF_TIME))


def load_edit_options(slicers: list[Slicer]) -> EditOptions:
    """Versions and plugins every selected slicer can use.

    Versions depend on region and plugins on account, so a mixed batch only
    offers what they all have.
    """
    targets = [t for t in _targets(slicers) if t.client]
    region_targets = list({(t.account_label, t.region): t for t in targets}.values())
    account_targets = list({t.account_label: t for t in targets}.values())

    def versions(target: _Target):
        try:
            return target.client.list_versions(target.region), None
        except UplynkAPIError as e:
            return None, f"Couldn't list versions for {target.account_label}: {e}"

    def plugins(target: _Target):
        try:
            return target.client.list_plugins(), None
        except UplynkAPIError as e:
            return None, f"Couldn't list plugins for {target.account_label}: {e}"

    def out_of_time(target: _Target):
        return None, f"Couldn't list options for {target.account_label} in time."

    version_results = _run_all(region_targets, versions, out_of_time)
    plugin_results = _run_all(account_targets, plugins, out_of_time)
    warnings = [w for _, w in version_results + plugin_results if w]

    version_lists = [v for v, _ in version_results if v is not None]
    common_versions = version_lists[0] if version_lists else []
    for other in version_lists[1:]:
        common_versions = [v for v in common_versions if v in other]

    plugin_maps = [p for p, _ in plugin_results if p is not None]
    common_plugins = dict(plugin_maps[0]) if plugin_maps else {}
    for other in plugin_maps[1:]:
        common_plugins = {
            plugin_id: [v for v in plugin_versions if v in other[plugin_id]]
            for plugin_id, plugin_versions in common_plugins.items()
            if plugin_id in other
        }
    return EditOptions(common_versions, common_plugins, warnings)


def _fetch_plan(target: _Target, update: UpdateRequest) -> SlicerPlan:
    if target.problem:
        return SlicerPlan(target.pk, target.slicer_id, target.account_label, error=target.problem)
    try:
        state, config = target.client.retrieve_slicer_snapshot(target.slicer_id)
    except UplynkAPIError as e:
        return SlicerPlan(target.pk, target.slicer_id, target.account_label, error=str(e))
    return build_plan(target, state, config, update)


def plan_updates(slicers: list[Slicer], update: UpdateRequest) -> list[SlicerPlan]:
    """What the update would do to each slicer, from its settings right now."""
    return _run_all(
        _targets(slicers),
        lambda t: _fetch_plan(t, update),
        lambda t: SlicerPlan(t.pk, t.slicer_id, t.account_label, error=OUT_OF_TIME),
    )


def apply_updates(
    user: User,
    slicers: list[Slicer],
    update: UpdateRequest,
    *,
    confirmed_restarts: set[int],
) -> list[UpdateResult]:
    """Send the update to each slicer, one PATCH each, and record what happened.

    One failure doesn't stop the others. A slicer that started running after
    the review isn't restarted without confirmation.
    """
    api_base = current_app.config["UPLYNK_API_BASE"]

    def apply(target: _Target) -> UpdateResult:
        plan = _fetch_plan(target, update)
        if plan.error:
            return UpdateResult(
                plan,
                STATUS_NOT_SENT,
                StatusExplanation(
                    "Not sent",
                    "The console couldn't read this slicer's current settings, "
                    "so nothing was sent.",
                    "Fix the problem below, then retry.",
                    plan.error,
                ),
            )
        if plan.skipped:
            return UpdateResult(plan, STATUS_SKIPPED)
        if plan.restarts and target.pk not in confirmed_restarts:
            return UpdateResult(
                plan,
                STATUS_NOT_SENT,
                StatusExplanation(
                    "Not sent",
                    "This slicer started running after the review, and updating it would "
                    "restart it.",
                    "Start a new update to review and confirm the restart.",
                ),
            )
        try:
            outcome = patch_slicer(
                api_base,
                target.slicer_id,
                plan.fields,
                timeout=PATCH_TIMEOUT_SECONDS,
                **target.key,
            )
        except UplynkAPIError as e:
            return UpdateResult(plan, STATUS_FAILED, explain_no_response(str(e)))
        return UpdateResult(
            plan,
            STATUS_UPDATED if outcome.ok else STATUS_FAILED,
            explain_status(
                outcome.status_code,
                outcome.body,
                account_label=target.account_label,
                running=plan.running,
            ),
            outcome.status_code,
            outcome.body,
        )

    def out_of_time(target: _Target) -> UpdateResult:
        return UpdateResult(
            SlicerPlan(target.pk, target.slicer_id, target.account_label),
            STATUS_NOT_SENT,
            StatusExplanation(
                "Not sent",
                OUT_OF_TIME,
                "This batch used up the time one request is allowed. Retry the remaining slicers.",
            ),
        )

    results = _run_all(_targets(slicers), apply, out_of_time)
    by_pk = {s.id: s for s in slicers}
    for result in results:
        _record(user, by_pk[result.plan.pk], result)
    db.session.commit()
    return results


def _record(user: User, slicer: Slicer, result: UpdateResult) -> None:
    """Audit an attempt and keep the stored configuration in step. Caller commits."""
    if result.status == STATUS_SKIPPED or result.explanation is None:
        return
    if result.status == STATUS_UPDATED:
        _store_updated_config(slicer, result)
    db.session.add(
        AuditEvent(
            user_id=user.id,
            slicer_id=slicer.id,
            method=AUDIT_METHOD,
            status_code=result.status_code,
            response_snippet=audit_summary(result),
            dry_run=False,
        )
    )


def _store_updated_config(slicer: Slicer, result: UpdateResult) -> None:
    """Replace the stored copy from the response, so stopped slicers show the change.

    A running slicer reloads its configuration as it restarts, so also capture
    again on the next running poll.
    """
    slicer.config_capture_pending = result.plan.running
    body = result.body
    if not isinstance(body, dict) or "configuration" not in body:
        slicer.config_capture_pending = True
        return
    slicer.config_encrypted = encrypt(SlicerConfig.from_api(body).to_json())
    slicer.config_captured_at = datetime.now(UTC)
    slicer.config_source = CONFIG_SOURCE_UPDATE
    slicer.config_error = None


def audit_summary(result: UpdateResult) -> str:
    """One audit line: the outcome, then old → new for everything attempted."""
    parts = [result.explanation.heading]
    if result.explanation.uplynk_message:
        parts.append(result.explanation.uplynk_message)
    parts += [f"{c.field}: {c.before} → {c.after}" for c in result.plan.changes]
    if result.plan.diff:
        lines = "; ".join(f"{'-' if d.removed else '+'}{d.text}" for d in result.plan.diff)
        parts.append(f"configuration: {lines}")
    return " | ".join(parts)[:2000]
