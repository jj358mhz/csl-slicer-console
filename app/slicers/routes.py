"""Slicer control routes — HTMX endpoints called from the dashboard."""

from __future__ import annotations

import time

import requests
from flask import Blueprint, Response, abort, render_template, request
from flask_login import current_user, login_required

from app.models import Slicer, UplynkAccount, db
from app.slicers.service import (
    SlicerAccessDenied,
    control_slicer,
    poll_account_slicer_states,
    poll_slicer_state,
    set_target_state,
    user_can_control,
)
from app.uplynk.csl import SLICER_METHODS
from app.uplynk.discovery import UplynkAPIError
from app.uplynk.target_state import TARGET_STATE_METHODS

bp = Blueprint("slicers", __name__, url_prefix="/slicers")

# In-process cache for the thumbnail proxy: {slicer_id: (thumb_url, content,
# content_type, fetched_at)}. Collapses duplicate upstream fetches when
# multiple viewers poll the same slicer within the same short window — real
# thumbnails regenerate every few seconds, so there's no point re-fetching
# more often than that. Per-worker-process only (not shared across gunicorn
# workers); a shared cache would be the next step if worker count grows.
_THUMB_CACHE: dict[int, tuple[str, bytes, str, float]] = {}
_THUMB_CACHE_TTL_SECONDS = 8


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


@bp.route("/<int:slicer_id>/thumb", methods=["GET"])
@login_required
def thumb(slicer_id: int):
    """GET /slicers/<id>/thumb — proxies the slicer's live thumbnail image.

    Uplynk serves thumb_url over plain HTTP; fetching it server-side and
    streaming the bytes back avoids mixed-content blocking on an
    HTTPS-served console and keeps the raw Uplynk CDN URL off the page.
    """
    slicer = db.session.get(Slicer, slicer_id)
    if slicer is None or not slicer.thumb_url:
        abort(404)
    if not user_can_control(current_user, slicer):
        abort(403)

    now = time.monotonic()
    cached = _THUMB_CACHE.get(slicer_id)
    if cached is not None:
        cached_url, content, content_type, fetched_at = cached
        if cached_url == slicer.thumb_url and (now - fetched_at) < _THUMB_CACHE_TTL_SECONDS:
            return Response(content, mimetype=content_type)

    try:
        upstream = requests.get(slicer.thumb_url, timeout=5)
        upstream.raise_for_status()
    except requests.RequestException:
        abort(502)

    content_type = upstream.headers.get("Content-Type", "image/jpeg")
    _THUMB_CACHE[slicer_id] = (slicer.thumb_url, upstream.content, content_type, now)

    return Response(upstream.content, mimetype=content_type)


@bp.route("/accounts/<int:account_id>/slicer-states", methods=["GET"])
@login_required
def account_states(account_id: int):
    """GET /slicers/accounts/<id>/slicer-states — coalesced HTMX polling endpoint.

    Fetches all slicer states for one Uplynk account in a single API call and
    returns an HTML fragment with OOB swap targets for every slicer the
    current user can see. Called once per account section on the dashboard
    instead of once per slicer card.
    """
    account = db.session.get(UplynkAccount, account_id)
    if account is None:
        abort(404)

    try:
        visible = poll_account_slicer_states(current_user, account)
    except SlicerAccessDenied:
        abort(403)
    except UplynkAPIError:
        # Uplynk hiccup — render whatever we last saw for each visible slicer
        # so no badge disappears. Matches poll_slicer_state's stale-on-error
        # policy. Rebuild the visible list without hitting Uplynk.
        if current_user.is_admin:
            visible = [s for s in account.slicers if s.is_active]
        else:
            assigned_ids = {s.id for s in current_user.slicers}
            visible = [s for s in account.slicers if s.is_active and s.id in assigned_ids]

    return render_template("slicers/_account_state.html", slicers=visible)
