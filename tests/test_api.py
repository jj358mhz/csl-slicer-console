"""Tests for the read-only settings API the player uses."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.crypto import encrypt
from app.models import Slicer, UplynkAccount, db
from app.uplynk.slicer_config import SlicerConfig

TOKEN = "test-api-token"
URL = "/api/slicers/s1/settings"


@pytest.fixture
def api_app(app):
    app.config["PLAYER_API_TOKEN"] = TOKEN
    return app


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _make_slicer(app, configuration=None, captured=True, slicer_id="s1", label="Acct"):
    with app.app_context():
        acct = UplynkAccount(
            label=label, workspace_id=f"ws-{label}", legacy_api_key_encrypted=encrypt("k")
        )
        db.session.add(acct)
        db.session.flush()
        slicer = Slicer(
            uplynk_account_id=acct.id,
            slicer_id=slicer_id,
            slicer_api_url="https://ingest.example.com/s1",
            is_active=True,
        )
        if captured:
            config = SlicerConfig(passphrase="secret-pass", configuration=configuration)
            slicer.config_encrypted = encrypt(config.to_json())
            slicer.config_captured_at = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
            slicer.config_source = "start"
        db.session.add(slicer)
        db.session.commit()
        return slicer.id


def test_off_without_a_token(app, client):
    _make_slicer(app, "wallclock: 1")
    assert client.get(URL, headers=_auth()).status_code == 404


@pytest.mark.parametrize("headers", [{}, _auth("wrong"), {"Authorization": TOKEN}])
def test_rejects_a_bad_token(api_app, client, headers):
    _make_slicer(api_app, "wallclock: 1")
    response = client.get(URL, headers=headers)
    assert response.status_code == 401
    assert "wallclock" not in response.get_data(as_text=True)


def test_wallclock_on(api_app, client):
    _make_slicer(api_app, "port: 5000\nwallclock: 1\nadmin_url: http://10.0.0.1:65009")
    body = client.get(URL, headers=_auth()).get_json()
    assert body["wallclock"] is True
    assert body["settings"] == {"wallclock": "1"}
    assert body["captured_at"] == "2026-10-19T12:00:00Z"
    assert body["source"] == "start"


def test_wallclock_off_when_absent(api_app, client):
    _make_slicer(api_app, "port: 5000")
    body = client.get(URL, headers=_auth()).get_json()
    assert body["wallclock"] is False
    assert body["settings"] == {}


def test_wallclock_zero(api_app, client):
    _make_slicer(api_app, "wallclock: 0")
    assert client.get(URL, headers=_auth()).get_json()["wallclock"] is False


def test_wallclock_unknown_without_a_capture(api_app, client):
    _make_slicer(api_app, captured=False)
    body = client.get(URL, headers=_auth()).get_json()
    assert body["wallclock"] is None
    assert body["captured_at"] is None


def test_never_returns_secrets(api_app, client):
    _make_slicer(api_app, "wallclock: 1\nadmin_url: http://10.0.0.1:65009")
    text = client.get(URL, headers=_auth()).get_data(as_text=True)
    assert "secret-pass" not in text
    assert "10.0.0.1" not in text


def test_unknown_slicer(api_app, client):
    response = client.get("/api/slicers/nope/settings", headers=_auth())
    assert response.status_code == 404


def test_same_slicer_in_two_accounts_uses_a_capture(api_app, client):
    _make_slicer(api_app, captured=False, label="A")
    _make_slicer(api_app, "wallclock: 1", label="B")
    assert client.get(URL, headers=_auth()).get_json()["wallclock"] is True
