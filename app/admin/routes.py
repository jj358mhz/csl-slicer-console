"""Admin routes: manage Uplynk accounts."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for

from app.admin.decorators import admin_required
from app.admin.forms import UplynkAccountForm
from app.crypto import decrypt, encrypt, mask
from app.models import UplynkAccount, db
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


@bp.route("/uplynk-accounts")
@admin_required
def list_accounts():
    accounts = (
        db.session.query(UplynkAccount)
        .order_by(UplynkAccount.label)
        .all()
    )
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
    form.legacy_api_key.data = ""  # never prefill secret fields

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
        flash(f"Sync failed for '{acct.label}': {result.error}", "error")
    else:
        flash(
            f"Synced '{acct.label}': "
            f"{result.created} new, {result.updated} updated, "
            f"{result.deactivated} deactivated.",
            "success",
        )
    return redirect(url_for("admin.list_accounts"))