"""Public routes: index and health check."""

from __future__ import annotations

from flask import jsonify

from app.main import bp


@bp.route("/")
def index() -> str:
    return (
        "<h1>csl-slicer-console</h1>"
        "<p>Under construction. See <code>/health</code>.</p>"
    )


@bp.route("/health")
def health():
    return jsonify(status="ok")
