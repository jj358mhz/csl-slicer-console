"""Channel health from the player test harness (its /api/health/slicers).

Server to server with the same shared token the player uses to read this
console's settings API (`PLAYER_API_TOKEN`). Answers are cached for a
minute per worker; any failure means no badges, never an error page.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests
from flask import current_app

TIMEOUT_S = 3
CACHE_TTL_S = 60

#: Worst first, for a slicer that feeds several channels.
SEVERITY = {"error": 0, "stale": 1, "behind": 2, "unknown": 3, "ok": 4}

_cache: tuple[float, dict[str, list[dict[str, Any]]]] | None = None
_lock = threading.Lock()


def enabled() -> bool:
    return bool(current_app.config["PLAYER_API_URL"] and current_app.config["PLAYER_API_TOKEN"])


def channel_health() -> dict[str, list[dict[str, Any]]]:
    """Health per slicer id: the player's channels for it, worst first."""
    global _cache
    if not enabled():
        return {}
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache[0] < CACHE_TTL_S:
            return _cache[1]
    by_slicer = _fetch()
    with _lock:
        _cache = (now, by_slicer)
    return by_slicer


def _fetch() -> dict[str, list[dict[str, Any]]]:
    url = f"{current_app.config['PLAYER_API_URL']}/api/health/slicers"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {current_app.config['PLAYER_API_TOKEN']}"},
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.RequestException, ValueError, AttributeError) as e:
        current_app.logger.warning("Player health unavailable: %s", e)
        return {}
    by_slicer: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if isinstance(item, dict) and item.get("slicer_id") and item.get("status"):
            by_slicer.setdefault(item["slicer_id"], []).append(item)
    for channels in by_slicer.values():
        channels.sort(key=lambda c: SEVERITY.get(c["status"], 9))
    return by_slicer


def fmt_age(seconds: float | None) -> str:
    """32 s, 4 m, 2 h 5 m (the player's wording)."""
    if seconds is None:
        return ""
    s = max(0, round(seconds))
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} m"
    return f"{s // 3600} h {s % 3600 // 60} m"


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None
