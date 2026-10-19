"""Slicer facts for the player test harness, by bearer token.

Off unless `PLAYER_API_TOKEN` is set. Answers only with allowlisted
configuration keys: the stored config also holds the passphrase and
ingest URLs, which never leave the console.
"""

from __future__ import annotations

import hmac

from flask import abort, current_app, jsonify, request

from app.api import bp
from app.models import Slicer, db
from app.slicers.service import stored_config

# Configuration keys other services may read. `wallclock: 1` makes the
# slicer write the encoder time into each segment's ID3 (the player's
# ID3 latency needs it).
SHARED_SETTINGS = ("wallclock",)

_TRUE = {"1", "true", "yes", "on"}


def _authorized() -> bool:
    token = current_app.config["PLAYER_API_TOKEN"]
    if not token:
        abort(404)
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(presented.strip(), token)


@bp.route("/slicers/<path:name>/settings", methods=["GET"])
def slicer_settings(name: str):
    """GET /api/slicers/<slicer>/settings — the slicer's shared settings.

    `wallclock` is true/false from the last captured config, or null when
    the console holds no config for the slicer yet.
    """
    if not _authorized():
        return jsonify(error="unauthorized"), 401

    slicers = db.session.query(Slicer).filter_by(slicer_id=name, is_active=True).all()
    if not slicers:
        return jsonify(error=f"no slicer {name} in this console"), 404
    # The same slicer id under two accounts: use the newest capture.
    slicer = max(slicers, key=lambda s: (s.config_captured_iso or "", s.id))

    config = stored_config(slicer)
    settings: dict[str, str] = {}
    if config is not None:
        for key, value in config.settings:
            if key in SHARED_SETTINGS and key not in settings:
                settings[key] = value
    wallclock = None if config is None else settings.get("wallclock", "").lower() in _TRUE
    return jsonify(
        slicer_id=slicer.slicer_id,
        captured_at=slicer.config_captured_iso,
        source=slicer.config_source,
        running=slicer.is_running,
        settings=settings,
        wallclock=wallclock,
    )
