"""Slicer control routes — HTMX endpoints called from the dashboard."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from app.models import Slicer, db
from app.slicers.service import (
    SlicerAccessDenied,
    control_slicer,
    poll_slicer_state,
    set_target_state,
)
from app.uplynk.csl import SLICER_METHODS
from app.uplynk.discovery import UplynkAPIError
from app.uplynk.target_state import TARGET_STATE_METHODS

bp = Blueprint("slicers", __name__, url_prefix="/slicers")


@bp.route("/<int:slicer_id>/control", methods=["POST"])
@login_required
def control(slicer_id: int):
    """POST /slicers/<id>/control with form field 'method'.

    Dispatches by method name:
    - SHA1 control methods (status/state/content_start/blackout) → control_slicer
    - v4 target-state methods (start/stop) → set_target_state

    Returns a small HTML fragment (for HTMX to swap in) showing the result.
    """
    slicer = db.session.get(Slicer, slicer_id)
    if slicer is None:
        abort(404)

    method_name = request.form.get("method", "").strip()
    dry_run = request.form.get("dry_run") == "1"

    if method_name in SLICER_METHODS:
        service_fn = control_slicer
    elif method_name in TARGET_STATE_METHODS:
        service_fn = set_target_state
    else:
        abort(400)

    try:
        outcome = service_fn(current_user, slicer, method_name, dry_run=dry_run)
    except SlicerAccessDenied:
        abort(403)

    return render_template(
        "slicers/_result.html",
        slicer=slicer,
        method_name=method_name,
        outcome=outcome,
        dry_run=dry_run,
    )


@bp.route("/<int:slicer_id>/state", methods=["GET"])
@login_required
def state(slicer_id: int):
    """GET /slicers/<id>/state — HTMX polling endpoint.

    Fetches current state from Uplynk, persists it, and returns the badge
    + tile label as an HTML fragment for HTMX to swap in place.
    """
    slicer = db.session.get(Slicer, slicer_id)
    if slicer is None:
        abort(404)

    try:
        poll_slicer_state(current_user, slicer)
    except SlicerAccessDenied:
        abort(403)
    except UplynkAPIError:
        # Uplynk hiccup — render whatever we last saw so the badge doesn't
        # disappear or flash "error". If this becomes noisy we'll add
        # a subtle stale indicator; for now just serve the stale row.
        pass

    return render_template("slicers/_state.html", s=slicer)
