"""Slicer control routes — HTMX endpoints called from the dashboard."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from app.models import Slicer, db
from app.slicers.service import SlicerAccessDenied, control_slicer
from app.uplynk.csl import SLICER_METHODS

bp = Blueprint("slicers", __name__, url_prefix="/slicers")


@bp.route("/<int:slicer_id>/control", methods=["POST"])
@login_required
def control(slicer_id: int):
    """POST /slicers/<id>/control with form field 'method'.

    Returns a small HTML fragment (for HTMX to swap in) showing the result.
    """
    slicer = db.session.get(Slicer, slicer_id)
    if slicer is None:
        abort(404)

    method_name = request.form.get("method", "").strip()
    if method_name not in SLICER_METHODS:
        abort(400)

    dry_run = request.form.get("dry_run") == "1"

    try:
        outcome = control_slicer(current_user, slicer, method_name, dry_run=dry_run)
    except SlicerAccessDenied:
        abort(403)

    return render_template(
        "slicers/_result.html",
        slicer=slicer,
        method_name=method_name,
        outcome=outcome,
        dry_run=dry_run,
    )
