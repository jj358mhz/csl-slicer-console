"""Admin routes: manage Uplynk accounts and users."""

from __future__ import annotations

import time
from dataclasses import replace
from itertools import groupby

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.admin.audit import log_admin_event
from app.admin.decorators import admin_required
from app.admin.forms import UplynkAccountForm, UserForm, UserSlicerAssignmentForm
from app.auth.passwords import hash_password
from app.crypto import decrypt, encrypt, mask
from app.models import Slicer, SlicerStar, UplynkAccount, User, db
from app.slicers import updates
from app.slicers.service import stored_config
from app.uplynk.scoped_env import ScopedEnvParseError, parse_scoped_env
from app.uplynk.slicer_config import normalize_configuration, without_description

bp = Blueprint("admin", __name__, url_prefix="/admin")

SYNC_ALL_BUDGET_SECONDS = 12


def _apply_scoped_env(acct: UplynkAccount, file_storage) -> str | None:
    """Read the uploaded .env file and populate the scoped-key fields.

    Returns an error message string on failure, None on success.
    Does nothing (returns None) if no file was uploaded.
    """
    if not file_storage or not file_storage.filename:
        return None
    try:
        parsed = parse_scoped_env(file_storage.read())
    except ScopedEnvParseError as e:
        return f"Scoped API Key file is invalid: {e}"
    acct.scoped_kid = parsed.kid
    acct.scoped_sub = parsed.sub
    acct.scoped_private_b64_encrypted = encrypt(parsed.private_b64)
    acct.scoped_scp = parsed.scp
    return None


# ---------------------------------------------------------------------------
# Uplynk account management
# ---------------------------------------------------------------------------


@bp.route("/uplynk-accounts")
@admin_required
def list_accounts():
    accounts = db.session.query(UplynkAccount).order_by(UplynkAccount.label).all()
    previews = {}
    for acct in accounts:
        try:
            previews[acct.id] = {
                "legacy": mask(decrypt(acct.legacy_api_key_encrypted)),
                "scoped_kid": acct.scoped_kid if acct.has_scoped_key else None,
            }
        except Exception:
            previews[acct.id] = {"legacy": "(decrypt failed)", "scoped_kid": None}
    return render_template(
        "admin/uplynk_accounts_list.html",
        accounts=accounts,
        previews=previews,
    )


@bp.route("/uplynk-accounts/new", methods=["GET", "POST"])
@admin_required
def new_account():
    form = UplynkAccountForm()
    if form.validate_on_submit():
        if not form.legacy_api_key.data:
            flash("Legacy API Key is required when creating a new account.", "error")
        else:
            acct = UplynkAccount(
                label=form.label.data.strip(),
                workspace_id=form.workspace_id.data.strip(),
                legacy_api_key_encrypted=encrypt(form.legacy_api_key.data),
            )
            err = _apply_scoped_env(acct, form.scoped_env_file.data)
            if err:
                flash(err, "error")
            else:
                db.session.add(acct)
                db.session.commit()
                log_admin_event(
                    "uplynk_account",
                    "create",
                    f"Created Uplynk account '{acct.label}'",
                    target=acct.label,
                )
                flash(f"Uplynk account '{acct.label}' created.", "success")
                return redirect(url_for("admin.list_accounts"))
    return render_template(
        "admin/uplynk_account_form.html",
        form=form,
        mode="new",
    )


@bp.route("/uplynk-accounts/<int:account_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_account(account_id: int):
    acct = db.session.get(UplynkAccount, account_id)
    if acct is None:
        abort(404)

    form = UplynkAccountForm(obj=acct)
    form.legacy_api_key.data = ""

    if form.validate_on_submit():
        acct.label = form.label.data.strip()
        acct.workspace_id = form.workspace_id.data.strip()
        if form.legacy_api_key.data:
            acct.legacy_api_key_encrypted = encrypt(form.legacy_api_key.data)
        err = _apply_scoped_env(acct, form.scoped_env_file.data)
        if err:
            flash(err, "error")
        else:
            db.session.commit()
            log_admin_event(
                "uplynk_account",
                "update",
                f"Updated Uplynk account '{acct.label}'",
                target=acct.label,
            )
            flash(f"Uplynk account '{acct.label}' updated.", "success")
            return redirect(url_for("admin.list_accounts"))

    return render_template(
        "admin/uplynk_account_form.html",
        form=form,
        mode="edit",
        account=acct,
    )


@bp.route("/uplynk-accounts/<int:account_id>/delete", methods=["POST"])
@admin_required
def delete_account(account_id: int):
    acct = db.session.get(UplynkAccount, account_id)
    if acct is None:
        abort(404)
    label = acct.label
    db.session.delete(acct)
    db.session.commit()
    log_admin_event("uplynk_account", "delete", f"Deleted Uplynk account '{label}'", target=label)
    flash(f"Uplynk account '{label}' deleted.", "info")
    return redirect(url_for("admin.list_accounts"))


def _sync_and_log(acct: UplynkAccount):
    """Sync one account and record the outcome in the audit log."""
    from app.uplynk.sync import sync_account

    result = sync_account(acct)

    if result.error:
        log_admin_event(
            "sync", "error", f"Sync failed for '{acct.label}': {result.error}", target=acct.label
        )
    else:
        log_admin_event(
            "sync",
            "run",
            f"Synced '{acct.label}': {result.created} new, {result.updated} updated, {result.deactivated} deactivated",
            target=acct.label,
        )
    return result


def _sync_and_report(acct: UplynkAccount) -> None:
    """Sync one account and flash the outcome."""
    result = _sync_and_log(acct)
    if result.error:
        flash(f"Sync failed for '{acct.label}': {result.error}", "error")
    else:
        flash(
            f"Synced '{acct.label}': "
            f"{result.created} new, {result.updated} updated, "
            f"{result.deactivated} deactivated.",
            "success",
        )


@bp.route("/uplynk-accounts/<int:account_id>/sync", methods=["POST"])
@admin_required
def sync_account_route(account_id: int):
    acct = db.session.get(UplynkAccount, account_id)
    if acct is None:
        abort(404)

    # The progress panel (static/js/sync_progress.js) asks for JSON.
    if request.headers.get("Accept") == "application/json":
        result = _sync_and_log(acct)
        return jsonify(
            label=acct.label,
            created=result.created,
            updated=result.updated,
            deactivated=result.deactivated,
            error=result.error,
        )

    _sync_and_report(acct)

    if request.form.get("return_to") == "slicer_updates":
        return redirect(url_for("admin.slicer_updates", slicer_ids=_posted_pks()))
    if request.form.get("return_to") == "dashboard":
        return redirect(url_for("main.index"))
    return redirect(url_for("admin.list_accounts"))


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


@bp.route("/users")
@admin_required
def list_users():
    users = db.session.query(User).order_by(User.email).all()
    return render_template("admin/users_list.html", users=users)


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    form = UserForm()
    if form.validate_on_submit():
        if not form.password.data:
            flash("Password is required when creating a new user.", "error")
        elif db.session.query(User).filter_by(email=form.email.data.lower().strip()).first():
            flash("A user with that email already exists.", "error")
        else:
            user = User(
                email=form.email.data.lower().strip(),
                password_hash=hash_password(form.password.data),
                is_admin=form.is_admin.data,
                is_active=form.is_active.data,
            )
            db.session.add(user)
            db.session.commit()
            log_admin_event(
                "user",
                "create",
                f"Created user '{user.email}'{' (admin)' if user.is_admin else ''}",
                target=user.email,
            )
            flash(f"User '{user.email}' created.", "success")
            return redirect(url_for("admin.list_users"))
    return render_template("admin/user_form.html", form=form, mode="new")


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = UserForm(obj=user)
    form.password.data = ""

    if form.validate_on_submit():
        new_email = form.email.data.lower().strip()
        clash = db.session.query(User).filter(User.email == new_email, User.id != user.id).first()
        if clash:
            flash("Another user already has that email.", "error")
        else:
            if user.id == current_user.id and (not form.is_admin.data or not form.is_active.data):
                flash("You can't remove your own admin status or deactivate yourself.", "error")
            else:
                user.email = new_email
                user.is_admin = form.is_admin.data
                user.is_active = form.is_active.data
                if form.password.data:
                    user.password_hash = hash_password(form.password.data)
                db.session.commit()
                log_admin_event("user", "update", f"Updated user '{user.email}'", target=user.email)
                flash(f"User '{user.email}' updated.", "success")
                return redirect(url_for("admin.list_users"))

    return render_template("admin/user_form.html", form=form, mode="edit", user=user)


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        flash("You can't delete yourself.", "error")
        return redirect(url_for("admin.list_users"))
    email = user.email
    db.session.delete(user)
    db.session.commit()
    log_admin_event("user", "delete", f"Deleted user '{email}'", target=email)
    flash(f"User '{email}' deleted.", "info")
    return redirect(url_for("admin.list_users"))


# ---------------------------------------------------------------------------
# Slicer assignment
# ---------------------------------------------------------------------------


@bp.route("/users/<int:user_id>/slicers", methods=["GET", "POST"])
@admin_required
def assign_slicers(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    active_slicers = (
        db.session.query(Slicer)
        .filter_by(is_active=True)
        .join(UplynkAccount)
        .order_by(UplynkAccount.label, Slicer.slicer_id)
        .all()
    )

    form = UserSlicerAssignmentForm()
    form.slicer_ids.choices = [
        (s.id, f"[{s.uplynk_account.label}] {s.slicer_id} ({s.region})") for s in active_slicers
    ]

    if form.validate_on_submit():
        selected_ids = set(form.slicer_ids.data or [])
        user.slicers = [s for s in active_slicers if s.id in selected_ids]
        db.session.commit()
        log_admin_event(
            "user",
            "assign_slicers",
            f"Assigned {len(user.slicers)} slicer(s) to '{user.email}'",
            target=user.email,
        )
        flash(
            f"Slicer assignments updated for '{user.email}' ({len(user.slicers)} assigned).",
            "success",
        )
        return redirect(url_for("admin.list_users"))
    else:
        form.slicer_ids.data = [s.id for s in user.slicers]

    return render_template(
        "admin/user_slicers.html",
        form=form,
        user=user,
        slicers=active_slicers,
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@bp.route("/audit")
@admin_required
def audit_log():
    from flask import request

    from app.models import AdminEvent, AuditEvent

    tab = request.args.get("tab", "control")

    # For the filter dropdowns (shared across tabs)
    all_users = db.session.query(User).order_by(User.email).all()
    all_slicers = (
        db.session.query(Slicer)
        .join(UplynkAccount)
        .order_by(UplynkAccount.label, Slicer.slicer_id)
        .all()
    )

    if tab == "admin":
        actor_id = request.args.get("actor_id", type=int)
        category = request.args.get("category", "").strip() or None

        query = db.session.query(AdminEvent).order_by(AdminEvent.timestamp.desc())
        if actor_id:
            query = query.filter(AdminEvent.actor_id == actor_id)
        if category:
            query = query.filter(AdminEvent.category == category)
        admin_events = query.limit(500).all()

        return render_template(
            "admin/audit_log.html",
            tab="admin",
            admin_events=admin_events,
            all_users=all_users,
            all_slicers=all_slicers,
            filters={"actor_id": actor_id, "category": category},
            categories=["user", "uplynk_account", "slicer", "sync"],
        )

    # Default: slicer control tab
    user_id = request.args.get("user_id", type=int)
    slicer_id = request.args.get("slicer_id", type=int)
    method = request.args.get("method", "").strip() or None

    query = db.session.query(AuditEvent).order_by(AuditEvent.timestamp.desc())
    if user_id:
        query = query.filter(AuditEvent.user_id == user_id)
    if slicer_id:
        query = query.filter(AuditEvent.slicer_id == slicer_id)
    if method:
        query = query.filter(AuditEvent.method == method)
    events = query.limit(500).all()

    return render_template(
        "admin/audit_log.html",
        tab="control",
        events=events,
        all_users=all_users,
        all_slicers=all_slicers,
        filters={"user_id": user_id, "slicer_id": slicer_id, "method": method},
    )


# ---------------------------------------------------------------------------
# Manual slicer CRUD (fallback when no scoped API key)
# ---------------------------------------------------------------------------


@bp.route("/slicers")
@admin_required
def list_slicers():
    slicers = (
        db.session.query(Slicer)
        .join(UplynkAccount)
        .order_by(UplynkAccount.label, Slicer.slicer_id)
        .all()
    )
    return render_template("admin/slicers_list.html", slicers=slicers)


@bp.route("/slicers/new", methods=["GET", "POST"])
@admin_required
def new_slicer():
    from app.admin.forms import SlicerForm

    accounts = db.session.query(UplynkAccount).order_by(UplynkAccount.label).all()
    if not accounts:
        flash("Add an Uplynk account first before creating slicers.", "error")
        return redirect(url_for("admin.list_accounts"))

    form = SlicerForm()
    form.uplynk_account_id.choices = [(a.id, a.label) for a in accounts]

    if form.validate_on_submit():
        clash = (
            db.session.query(Slicer)
            .filter_by(
                uplynk_account_id=form.uplynk_account_id.data,
                slicer_id=form.slicer_id.data.strip(),
            )
            .first()
        )
        if clash:
            flash("A slicer with that ID already exists under that account.", "error")
        else:
            slicer = Slicer(
                uplynk_account_id=form.uplynk_account_id.data,
                slicer_id=form.slicer_id.data.strip(),
                slicer_api_url=form.slicer_api_url.data.strip(),
                region=form.region.data.strip() or None,
                protocol=form.protocol.data.strip() or None,
                is_active=True,
                is_manual=True,
            )
            db.session.add(slicer)
            db.session.commit()
            log_admin_event(
                "slicer",
                "create",
                f"Manually added slicer '{slicer.slicer_id}' under '{slicer.uplynk_account.label}'",
                target=slicer.slicer_id,
            )
            flash(f"Slicer '{slicer.slicer_id}' added.", "success")
            return redirect(url_for("admin.list_slicers"))

    return render_template("admin/slicer_form.html", form=form, mode="new")


@bp.route("/slicers/<int:slicer_id_pk>/edit", methods=["GET", "POST"])
@admin_required
def edit_slicer(slicer_id_pk: int):
    from app.admin.forms import SlicerForm

    slicer = db.session.get(Slicer, slicer_id_pk)
    if slicer is None:
        abort(404)

    accounts = db.session.query(UplynkAccount).order_by(UplynkAccount.label).all()
    form = SlicerForm(obj=slicer)
    form.uplynk_account_id.choices = [(a.id, a.label) for a in accounts]

    if form.validate_on_submit():
        slicer.uplynk_account_id = form.uplynk_account_id.data
        slicer.slicer_id = form.slicer_id.data.strip()
        slicer.slicer_api_url = form.slicer_api_url.data.strip()
        slicer.region = form.region.data.strip() or None
        slicer.protocol = form.protocol.data.strip() or None
        db.session.commit()
        log_admin_event(
            "slicer",
            "update",
            f"Updated manual slicer '{slicer.slicer_id}'",
            target=slicer.slicer_id,
        )
        flash(f"Slicer '{slicer.slicer_id}' updated.", "success")
        return redirect(url_for("admin.list_slicers"))

    return render_template("admin/slicer_form.html", form=form, mode="edit", slicer=slicer)


@bp.route("/slicers/<int:slicer_id_pk>/delete", methods=["POST"])
@admin_required
def delete_slicer(slicer_id_pk: int):
    slicer = db.session.get(Slicer, slicer_id_pk)
    if slicer is None:
        abort(404)
    name = slicer.slicer_id
    db.session.delete(slicer)
    db.session.commit()
    log_admin_event("slicer", "delete", f"Deleted manual slicer '{name}'", target=name)
    flash(f"Slicer '{name}' deleted.", "info")
    return redirect(url_for("admin.list_slicers"))


# ---------------------------------------------------------------------------
# Slicer Updates (#71): select, edit, review, apply. Each step is a POST that
# carries the request forward in hidden fields, and re-reads the slicers from
# Uplynk, so nothing is held on the server between steps.
# ---------------------------------------------------------------------------


def _posted_pks(name: str = "slicer_ids") -> list[int]:
    return [int(v) for v in request.values.getlist(name) if v.isdigit()]


@bp.route("/slicer-updates")
@admin_required
def slicer_updates():
    slicers = updates.list_updatable_slicers()
    starred = {
        slicer_id
        for (slicer_id,) in db.session.query(SlicerStar.slicer_id).filter_by(
            user_id=current_user.id
        )
    }
    # Grouped and ordered like the Dashboard: starred first within each workspace.
    slicers.sort(key=lambda s: (s.uplynk_account.label, s.id not in starred, s.slicer_id))
    current, unread = updates.fetch_current_listing(slicers)

    def row(slicer: Slicer) -> tuple:
        # Uplynk's live values, or the last captured copy if it couldn't say.
        if slicer.id in current:
            state, config = current[slicer.id]
            return slicer, config, state
        return slicer, stored_config(slicer), slicer.last_state

    groups = [
        (label, [row(s) for s in items])
        for label, items in groupby(slicers, key=lambda s: s.uplynk_account.label)
    ]
    problems = {
        s.uplynk_account_id: updates.account_write_problem(s.uplynk_account) for s in slicers
    }
    return render_template(
        "admin/slicer_updates_select.html",
        step=1,
        groups=groups,
        starred=starred,
        problems=problems,
        account_problems=sorted({p for p in problems.values() if p}),
        selected=set(_posted_pks()),
        unread=unread,
        sync_accounts=updates.syncable_accounts(),
    )


@bp.route("/slicer-updates/sync-all", methods=["POST"])
@admin_required
def slicer_updates_sync_all():
    """Sync every account with a scoped key, one at a time.

    The fallback without JavaScript: the progress panel syncs accounts one
    request at a time instead. Gunicorn kills a request after 30s and one sync
    can take 15s, so accounts not started within the budget are named for the
    admin to sync individually.
    """
    accounts = [
        a
        for a in db.session.scalars(select(UplynkAccount).order_by(UplynkAccount.label))
        if a.has_scoped_key
    ]
    deadline = time.monotonic() + SYNC_ALL_BUDGET_SECONDS
    not_reached = []
    for acct in accounts:
        if time.monotonic() > deadline:
            not_reached.append(acct.label)
        else:
            _sync_and_report(acct)
    if not_reached:
        flash(f"Ran out of time before syncing: {', '.join(not_reached)}.", "error")
    return redirect(url_for("admin.slicer_updates", slicer_ids=_posted_pks()))


def _updatable_selection() -> list[Slicer]:
    """The posted slicers whose accounts can be updated, flashing about the rest."""
    slicers = updates.selected_slicers(_posted_pks())
    usable = [s for s in slicers if not updates.account_write_problem(s.uplynk_account)]
    if len(usable) < len(slicers):
        flash("Slicers on accounts without a write key were left out.", "error")
    return usable


def _posted_update(slicers: list[Slicer]) -> updates.UpdateRequest:
    """The posted request. More than one slicer is always a batch, whatever was posted."""
    update = updates.UpdateRequest.from_form(request.form)
    return replace(update, batch=True) if len(slicers) > 1 else update


def _render_edit(slicers: list[Slicer], update: updates.UpdateRequest, error: str | None = None):
    batch = update.batch or len(slicers) > 1
    snapshots = updates.fetch_snapshots(slicers)
    readable = [(t, c) for t, c, _ in snapshots if c is not None]
    shared = [(t, without_description(c.configuration)) for t, c in readable]
    if batch:
        fresh_text = shared[0][1] if shared else ""
    else:
        fresh_text = normalize_configuration(readable[0][1].configuration) if readable else ""
    base = update.config_base or fresh_text
    return render_template(
        "admin/slicer_updates_edit.html",
        step=2,
        slicers=slicers,
        update=update,
        batch=batch,
        config_base=base,
        config_text=update.configuration or base,
        config_source=readable[0][0].slicer_id if readable else None,
        differing=[t.slicer_id for t, text in shared if text != (shared[0][1] if shared else "")],
        read_errors=[(t.slicer_id, e) for t, c, e in snapshots if c is None],
        options=updates.load_edit_options(slicers),
        error=error,
    )


@bp.route("/slicer-updates/edit", methods=["POST"])
@admin_required
def slicer_updates_edit():
    slicers = _updatable_selection()
    if not slicers:
        flash("Select at least one slicer.", "error")
        return redirect(url_for("admin.slicer_updates"))
    return _render_edit(slicers, _posted_update(slicers))


@bp.route("/slicer-updates/review", methods=["POST"])
@admin_required
def slicer_updates_review():
    slicers = _updatable_selection()
    if not slicers:
        flash("Select at least one slicer.", "error")
        return redirect(url_for("admin.slicer_updates"))
    update = _posted_update(slicers)
    if update.config_edited and not update.configuration:
        # Uplynk replaces the whole configuration, so empty would wipe it.
        return _render_edit(slicers, update, "Configuration can't be empty.")
    plans = updates.plan_updates(slicers, update)
    if not any(p.fields or p.error for p in plans):
        return _render_edit(slicers, update, "Change at least one setting to continue.")
    return render_template(
        "admin/slicer_updates_review.html",
        step=3,
        slicers=slicers,
        update=update,
        plans=plans,
        restarts=[p for p in plans if p.restarts],
        sending=sum(1 for p in plans if p.fields),
    )


@bp.route("/slicer-updates/apply", methods=["POST"])
@admin_required
def slicer_updates_apply():
    slicers = _updatable_selection()
    if not slicers:
        flash("Select at least one slicer.", "error")
        return redirect(url_for("admin.slicer_updates"))
    update = _posted_update(slicers)
    if update.config_edited and not update.configuration:
        return _render_edit(slicers, update, "Configuration can't be empty.")
    confirmed = set(_posted_pks("restart_ids")) if request.form.get("confirm_restart") else set()
    results = updates.apply_updates(current_user, slicers, update, confirmed_restarts=confirmed)
    return render_template(
        "admin/slicer_updates_results.html",
        step=4,
        update=update,
        results=results,
        retry_ids=[r.plan.pk for r in results if r.retryable],
        confirmed=confirmed,
        counts={
            status: sum(1 for r in results if r.status == status)
            for status in (
                updates.STATUS_UPDATED,
                updates.STATUS_FAILED,
                updates.STATUS_NOT_SENT,
                updates.STATUS_SKIPPED,
            )
        },
    )
