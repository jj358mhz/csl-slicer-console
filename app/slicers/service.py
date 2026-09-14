"""Slicer control service — wraps CSL calls with authorization + audit logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from flask import current_app

from app.crypto import decrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.uplynk.csl import SLICER_METHODS, CSLError, CSLResult, call_slicer
from app.uplynk.discovery import (
    DiscoveredSlicer,
    UplynkAPIError,
    UplynkDiscoveryClient,
)
from app.uplynk.target_state import (
    TARGET_STATE_METHODS,
    TargetStateResult,
    set_slicer_target_state,
)


class SlicerAccessDenied(RuntimeError):
    """Raised when a user tries to control a slicer they're not assigned."""


@dataclass
class ControlOutcome:
    """Result of a control attempt, including any audit event id."""

    result: CSLResult | None
    error: str | None = None
    audit_event_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok


def _user_can_control(user: User, slicer: Slicer) -> bool:
    """Admins can control any active slicer; regular users only assigned ones."""
    if not slicer.is_active:
        return False
    if user.is_admin:
        return True
    return any(s.id == slicer.id for s in user.slicers)


def user_can_control(user: User, slicer: Slicer) -> bool:
    """Public wrapper — same policy as _user_can_control, exported for routes."""
    return _user_can_control(user, slicer)


def control_slicer(
    user: User,
    slicer: Slicer,
    method_name: str,
    *,
    dry_run: bool = False,
) -> ControlOutcome:
    """Attempt a control action against one slicer, logging the outcome.

    Raises SlicerAccessDenied if the user isn't allowed to control this slicer.
    All other failure modes (bad method, decrypt error, HTTP error, network
    error) are captured into ControlOutcome.error and written to audit_events.
    """
    if not _user_can_control(user, slicer):
        raise SlicerAccessDenied(f"User {user.email} is not assigned to slicer {slicer.slicer_id}")

    method_path = SLICER_METHODS.get(method_name)
    if method_path is None:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=f"Unknown method '{method_name}'",
        )

    account = slicer.uplynk_account
    try:
        api_key = decrypt(account.legacy_api_key_encrypted)
    except Exception as e:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=f"Failed to decrypt API key: {e}",
        )

    if dry_run:
        current_app.logger.info("DRY RUN — would POST %s to %s", method_path, slicer.slicer_api_url)
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            result=CSLResult(status_code=0, body="[dry-run]", ok=True),
        )

    try:
        result = call_slicer(slicer.slicer_api_url, method_path, api_key)
    except CSLError as e:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=str(e),
        )

    return _record_and_return(
        user,
        slicer,
        method_name,
        dry_run,
        result=result,
    )


def _record_and_return(
    user: User,
    slicer: Slicer,
    method_name: str,
    dry_run: bool,
    *,
    result: CSLResult | None = None,
    error: str | None = None,
) -> ControlOutcome:
    """Persist an AuditEvent and return a ControlOutcome."""
    if result is not None:
        status_code = result.status_code
        snippet = result.summary
    else:
        status_code = None
        snippet = error or "unknown error"

    event = AuditEvent(
        user_id=user.id,
        slicer_id=slicer.id,
        method=method_name,
        status_code=status_code,
        response_snippet=snippet[:500] if snippet else None,
        dry_run=dry_run,
    )
    db.session.add(event)
    db.session.commit()

    return ControlOutcome(result=result, error=error, audit_event_id=event.id)


def _apply_fresh_state(slicer: Slicer, fresh: DiscoveredSlicer) -> bool:
    """Apply fresh state to a slicer row if anything changed.

    Returns True if the row was modified (caller must commit), False if the
    fresh values match what's already on the row (caller can skip the write).

    Only `last_state` and `connection_mode` drive the change decision; when
    they're unchanged we skip touching `last_seen_at` too, so an unchanged
    poll produces no DB write at all (see #15).
    """
    if slicer.last_state == fresh.state and slicer.connection_mode == fresh.connection_mode:
        return False
    slicer.last_state = fresh.state
    slicer.connection_mode = fresh.connection_mode
    slicer.last_seen_at = datetime.now(UTC)
    return True


def poll_slicer_state(user: User, slicer: Slicer) -> Slicer:
    """Fetch the slicer's current state from Uplynk and persist it.

    Uses the v4 retrieve endpoint (scoped-key JWT auth, orthogonal to the
    SHA1-signed control endpoints). Updates last_state, connection_mode, and
    last_seen_at on the row if any of them changed, commits, and returns the
    refreshed Slicer. No-op polls skip the commit entirely (see #15).

    Raises SlicerAccessDenied if the user isn't permitted to view this slicer.
    Raises UplynkAPIError on Uplynk-side failures — caller decides how to render.
    """
    if not _user_can_control(user, slicer):
        raise SlicerAccessDenied(f"User {user.email} is not assigned to slicer {slicer.slicer_id}")

    account = slicer.uplynk_account
    if not account.has_scoped_key:
        raise UplynkAPIError(f"Account {account.label!r} has no scoped API key — cannot poll state")

    private_b64 = decrypt(account.scoped_private_b64_encrypted)
    client = UplynkDiscoveryClient(
        api_base=current_app.config["UPLYNK_API_BASE"],
        kid=account.scoped_kid,
        sub=account.scoped_sub,
        private_b64=private_b64,
        scp=account.scoped_scp,
    )

    fresh = client.retrieve_slicer(slicer.slicer_id)

    # last_state uses the v4 retrieve vocabulary (see app.uplynk.states).
    # The SHA1 control /state endpoint uses a different vocabulary that only
    # lands in AuditEvent.response_snippet — never rendered as a badge.
    if _apply_fresh_state(slicer, fresh):
        db.session.commit()
    return slicer


def poll_account_slicer_states(user: User, account: UplynkAccount) -> list[Slicer]:
    """Fetch all slicer states for one account in a single Uplynk call.

    Replaces N per-slicer `retrieve_slicer()` calls with one `list_slicers()`
    call, then applies change-detection per slicer (see #15) so unchanged
    polls produce zero DB writes. One commit per poll regardless of slicer
    count.

    Access control mirrors the per-slicer path: admins see every active
    slicer on the account; regular users see only the subset they're
    assigned to. Slicers Uplynk didn't return in the list response are
    still included in the returned list with their existing (stale) row
    data — the badge template will render whatever we last saw, matching
    the single-slicer path's stale-on-error behavior.

    Raises SlicerAccessDenied if the user has no visible slicers on this
    account and isn't admin.
    Raises UplynkAPIError on Uplynk-side failures — caller decides how to
    render.
    """
    if user.is_admin:
        visible = [s for s in account.slicers if s.is_active]
    else:
        assigned_ids = {s.id for s in user.slicers}
        visible = [s for s in account.slicers if s.is_active and s.id in assigned_ids]

    if not visible:
        raise SlicerAccessDenied(
            f"User {user.email} has no assigned slicers on account {account.label!r}"
        )

    if not account.has_scoped_key:
        raise UplynkAPIError(
            f"Account {account.label!r} has no scoped API key — cannot poll states"
        )

    private_b64 = decrypt(account.scoped_private_b64_encrypted)
    client = UplynkDiscoveryClient(
        api_base=current_app.config["UPLYNK_API_BASE"],
        kid=account.scoped_kid,
        sub=account.scoped_sub,
        private_b64=private_b64,
        scp=account.scoped_scp,
    )

    fresh_by_id: dict[str, DiscoveredSlicer] = {f.slicer_id: f for f in client.list_slicers()}

    any_changed = False
    for slicer in visible:
        fresh = fresh_by_id.get(slicer.slicer_id)
        if fresh is None:
            # Uplynk didn't return this one — leave the row untouched so the
            # badge renders whatever we last saw, matching poll_slicer_state's
            # stale-on-error behavior.
            continue
        if _apply_fresh_state(slicer, fresh):
            any_changed = True

    if any_changed:
        db.session.commit()

    return visible


def set_target_state(
    user: User,
    slicer: Slicer,
    method_name: str,
    *,
    dry_run: bool = False,
) -> ControlOutcome:
    """Set a slicer's target_state via the v4 PATCH endpoint.

    method_name is 'start' or 'stop' (see TARGET_STATE_METHODS). Same
    authorization + audit shape as control_slicer(), just against a
    different Uplynk subsystem (v4 JWT/scoped key rather than SHA1/legacy).

    Raises SlicerAccessDenied if the user isn't allowed to control this
    slicer. HTTP-level failures land in ControlOutcome.error; network and
    scoped-key problems land in the same place.
    """
    if not _user_can_control(user, slicer):
        raise SlicerAccessDenied(f"User {user.email} is not assigned to slicer {slicer.slicer_id}")

    target_state = TARGET_STATE_METHODS.get(method_name)
    if target_state is None:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=f"Unknown target-state method '{method_name}'",
        )

    account = slicer.uplynk_account
    if not account.has_scoped_key:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=(
                f"Account {account.label!r} has no scoped API key — "
                "cannot set target_state (requires v4 API auth)"
            ),
        )

    if dry_run:
        current_app.logger.info(
            "DRY RUN — would PATCH target_state=%s on slicer %s",
            target_state,
            slicer.slicer_id,
        )
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            result=CSLResult(
                status_code=0,
                body=f"[dry-run] target_state={target_state}",
                ok=True,
            ),
        )

    try:
        private_b64 = decrypt(account.scoped_private_b64_encrypted)
    except Exception as e:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=f"Failed to decrypt scoped key: {e}",
        )

    try:
        outcome = set_slicer_target_state(
            api_base=current_app.config["UPLYNK_API_BASE"],
            slicer_id=slicer.slicer_id,
            target_state=target_state,
            kid=account.scoped_kid,
            sub=account.scoped_sub,
            private_b64=private_b64,
            scp=account.scoped_scp,
        )
    except UplynkAPIError as e:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=str(e),
        )

    return _record_and_return_target_state(user, slicer, method_name, dry_run, outcome)


def _record_and_return_target_state(
    user: User,
    slicer: Slicer,
    method_name: str,
    dry_run: bool,
    outcome: TargetStateResult,
) -> ControlOutcome:
    """Persist an AuditEvent for a target_state call.

    TargetStateResult has the same shape as CSLResult but is not the same
    type — this helper adapts it so ControlOutcome carries a consistent
    payload regardless of subsystem.
    """
    event = AuditEvent(
        user_id=user.id,
        slicer_id=slicer.id,
        method=method_name,
        status_code=outcome.status_code,
        response_snippet=outcome.summary[:500] if outcome.summary else None,
        dry_run=dry_run,
    )
    db.session.add(event)
    db.session.commit()

    # ControlOutcome expects a CSLResult; adapt so the route/template path
    # doesn't need to know which subsystem produced this.
    adapted = CSLResult(
        status_code=outcome.status_code,
        body=outcome.body,
        ok=outcome.ok,
    )
    return ControlOutcome(
        result=adapted if outcome.ok else None,
        error=None if outcome.ok else outcome.summary,
        audit_event_id=event.id,
    )
