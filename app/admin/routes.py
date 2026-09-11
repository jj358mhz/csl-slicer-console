"""Admin routes: manage Uplynk accounts and users."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from app.admin.audit import log_admin_event
from app.admin.decorators import admin_required
from app.admin.forms import UplynkAccountForm, UserForm, UserSlicerAssignmentForm
from app.auth.passwords import hash_password
from app.crypto import decrypt, encrypt, mask
from app.models import Slicer, UplynkAccount, User, db
from app.uplynk.scoped_env import ScopedEnvParseError, parse_scoped_env

bp = Blueprint("admin", __name__, url_prefix="/admin")


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


@bp.route("/uplynk-accounts/<int:account_id>/sync", methods=["POST"])
@admin_required
def sync_account_route(account_id: int):
    from app.uplynk.sync import sync_account

    acct = db.session.get(UplynkAccount, account_id)
    if acct is None:
        abort(404)

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

    if result.error:
        flash(f"Sync failed for '{acct.label}': {result.error}", "error")
    else:
        flash(
            f"Synced '{acct.label}': "
            f"{result.created} new, {result.updated} updated, "
            f"{result.deactivated} deactivated.",
            "success",
        )
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
