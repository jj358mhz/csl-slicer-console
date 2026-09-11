"""Admin routes: manage Uplynk accounts."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for

from app.admin.decorators import admin_required
from app.admin.forms import UplynkAccountForm
from app.crypto import decrypt, encrypt, mask
from app.models import UplynkAccount, db

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/uplynk-accounts")
@admin_required
def list_accounts():
    accounts = (
        db.session.query(UplynkAccount)
        .order_by(UplynkAccount.label)
        .all()
    )
    # Build a masked preview per account for display
    previews = {}
    for acct in accounts:
        try:
            previews[acct.id] = {
                "legacy": mask(decrypt(acct.legacy_api_key_encrypted)),
                "scoped": (
                    mask(decrypt(acct.scoped_api_key_encrypted))
                    if acct.scoped_api_key_encrypted
                    else None
                ),
            }
        except Exception:
            previews[acct.id] = {"legacy": "(decrypt failed)", "scoped": None}
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
                scoped_api_key_encrypted=(
                    encrypt(form.scoped_api_key.data)
                    if form.scoped_api_key.data
                    else None
                ),
            )
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
    # Never prefill password fields
    form.legacy_api_key.data = ""
    form.scoped_api_key.data = ""

    if form.validate_on_submit():
        acct.label = form.label.data.strip()
        acct.workspace_id = form.workspace_id.data.strip()
        if form.legacy_api_key.data:
            acct.legacy_api_key_encrypted = encrypt(form.legacy_api_key.data)
        if form.scoped_api_key.data:
            acct.scoped_api_key_encrypted = encrypt(form.scoped_api_key.data)
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
