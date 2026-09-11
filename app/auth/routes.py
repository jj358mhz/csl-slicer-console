"""Auth routes: login and logout."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from urllib.parse import urlparse

from app.auth.forms import LoginForm
from app.auth.passwords import verify_password
from app.models import User, db

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = (
            db.session.query(User)
            .filter_by(email=form.email.data.lower().strip())
            .first()
        )
        if user and user.is_active and verify_password(user.password_hash, form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get("next")
            # Prevent open-redirect: only allow relative paths on same host
            if next_page and urlparse(next_page).netloc == "":
                return redirect(next_page)
            return redirect(url_for("main.index"))
        flash("Invalid email or password.", "error")

    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You've been signed out.", "info")
    return redirect(url_for("auth.login"))