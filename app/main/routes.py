"""Public routes: index and health check."""

from __future__ import annotations

from flask import jsonify, render_template
from flask_login import login_required

from app.main import bp


@bp.route("/")
@login_required
def index() -> str:
    return render_template("main/index.html")


@bp.route("/health")
def health():
    return jsonify(status="ok")