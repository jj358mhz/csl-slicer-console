"""Channel health badges from the player test harness."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app import player
from app.crypto import encrypt
from app.models import Slicer, UplynkAccount, db

ITEMS = [
    {
        "slicer_id": "s1",
        "channel": 7,
        "name": "Chan A",
        "status": "ok",
        "edge_s": 31.2,
        "detail": None,
        "checked_at": "2026-10-19T12:00:00Z",
        "health_url": "/channels/7/health",
    },
    {
        "slicer_id": "s1",
        "channel": 8,
        "name": "Chan B",
        "status": "stale",
        "edge_s": 400.0,
        "detail": "newest segment is 6 m old",
        "checked_at": "2026-10-19T12:00:00Z",
        "health_url": "/channels/8/health",
    },
    {
        "slicer_id": "s2",
        "channel": 9,
        "name": "Chan C",
        "status": "ok",
        "edge_s": 29.0,
        "detail": None,
        "checked_at": "2026-10-19T12:00:00Z",
        "health_url": "/channels/9/health",
    },
]


@pytest.fixture
def configured(app, monkeypatch):
    app.config["PLAYER_BASE_URL"] = "https://player.example.com"
    app.config["PLAYER_API_URL"] = "http://player.internal:5000"
    app.config["PLAYER_API_TOKEN"] = "shared-test-token"
    player.clear_cache()
    get = MagicMock()
    get.return_value.json.return_value = {"items": ITEMS}
    get.return_value.raise_for_status.return_value = None
    monkeypatch.setattr(player.requests, "get", get)
    yield get
    player.clear_cache()


def test_off_without_token(app, monkeypatch):
    get = MagicMock()
    monkeypatch.setattr(player.requests, "get", get)
    assert player.channel_health() == {}
    get.assert_not_called()


def test_worst_first_and_cached(app, configured):
    health = player.channel_health()
    assert [c["status"] for c in health["s1"]] == ["stale", "ok"]
    assert list(health) == ["s1", "s2"]
    (url,) = configured.call_args.args
    assert url == "http://player.internal:5000/api/health/slicers"
    assert configured.call_args.kwargs["headers"] == {"Authorization": "Bearer shared-test-token"}
    player.channel_health()
    assert configured.call_count == 1  # cached


def test_failure_means_no_badges(app, configured):
    configured.side_effect = requests.ConnectionError("down")
    assert player.channel_health() == {}


def test_fmt_age():
    assert [player.fmt_age(s) for s in (None, 31.2, 400, 7300)] == ["", "31 s", "6 m", "2 h 1 m"]


def _login(client):
    client.post(
        "/auth/login",
        data={"email": "test@example.com", "password": "test-password-123", "submit": "Sign in"},
        follow_redirects=True,
    )


def _make_slicers(app):
    with app.app_context():
        acct = UplynkAccount(label="Acct", workspace_id="ws", legacy_api_key_encrypted=encrypt("k"))
        db.session.add(acct)
        db.session.flush()
        ids = []
        for name in ("s1", "s2", "s3"):
            s = Slicer(
                uplynk_account_id=acct.id,
                slicer_id=name,
                slicer_api_url=f"https://ingest.example.com/{name}",
                is_active=True,
            )
            db.session.add(s)
            db.session.flush()
            ids.append(s.id)
        db.session.commit()
        return acct.id, ids


def test_dashboard_badges(app, client, configured):
    _, (s1, s2, s3) = _make_slicers(app)
    _login(client)
    page = client.get("/").get_data(as_text=True)
    assert f'id="player-health-{s1}"' in page
    assert "badge--health--stale" in page and "● stale 6 m" in page
    assert "worst of 2 channels" in page
    assert 'href="https://player.example.com/channels/8/health"' in page
    assert "● 29 s" in page  # s2
    # s3 has no channel in the player: an empty placeholder for later OOB swaps.
    empty = page.split(f'id="player-health-{s3}"')[1].split("</span>")[0]
    assert "badge" not in empty


def test_polling_refreshes_badges(app, client, configured):
    acct_id, (s1, _, _) = _make_slicers(app)
    _login(client)
    from unittest.mock import patch

    with patch("app.slicers.routes.poll_account_slicer_states") as poll:
        poll.return_value = db.session.query(Slicer).all()
        body = client.get(f"/slicers/accounts/{acct_id}/slicer-states").get_data(as_text=True)
    assert f'id="player-health-{s1}" class="slicer-card__health" hx-swap-oob="true"' in body
    assert "badge--health--stale" in body
