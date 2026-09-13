"""Tests for the slicer control HTTP routes (HTMX endpoints)."""

from __future__ import annotations

from unittest.mock import patch

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.uplynk.csl import CSLResult
from app.uplynk.discovery import DiscoveredSlicer, UplynkAPIError


def _login(client, email="test@example.com", password="test-password-123"):
    client.post(
        "/auth/login",
        data={"email": email, "password": password, "submit": "Sign in"},
        follow_redirects=True,
    )


def _make_slicer(app):
    with app.app_context():
        acct = UplynkAccount(
            label="Acct",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("legacy-key"),
        )
        db.session.add(acct)
        db.session.flush()
        slicer = Slicer(
            uplynk_account_id=acct.id,
            slicer_id="s1",
            slicer_api_url="https://ingest.example.com/s1",
            is_active=True,
        )
        db.session.add(slicer)
        db.session.commit()
        return slicer.id


def test_control_requires_login(app, client):
    slicer_id = _make_slicer(app)
    response = client.post(
        f"/slicers/{slicer_id}/control",
        data={"method": "state"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_admin_can_call_status(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    with patch(
        "app.slicers.service.call_slicer",
        return_value=CSLResult(200, {"state": "Running"}, True),
    ):
        response = client.post(
            f"/slicers/{slicer_id}/control",
            data={"method": "status"},
        )
    assert response.status_code == 200
    assert b"OK" in response.data
    with app.app_context():
        assert db.session.query(AuditEvent).count() == 1


def test_regular_user_denied_on_unassigned_slicer(app, client):
    slicer_id = _make_slicer(app)
    with app.app_context():
        u = User(
            email="ru@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.commit()

    _login(client, "ru@example.com", "password123")
    response = client.post(
        f"/slicers/{slicer_id}/control",
        data={"method": "state"},
    )
    assert response.status_code == 403


def test_unknown_method_returns_400(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    response = client.post(
        f"/slicers/{slicer_id}/control",
        data={"method": "explode"},
    )
    assert response.status_code == 400


def test_dry_run_does_not_hit_api(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    with patch("app.slicers.service.call_slicer") as mock_call:
        response = client.post(
            f"/slicers/{slicer_id}/control",
            data={"method": "blackout", "dry_run": "1"},
        )
        mock_call.assert_not_called()
    assert response.status_code == 200
    with app.app_context():
        event = db.session.query(AuditEvent).one()
        assert event.dry_run is True


def test_dashboard_renders_control_buttons(app, client):
    _login(client)
    _make_slicer(app)
    response = client.get("/")
    assert response.status_code == 200
    # Buttons present for all four methods
    for method in ["status", "state", "content_start", "blackout"]:
        assert f'value="{method}"'.encode() in response.data
    # HTMX attribute present
    assert b"hx-post" in response.data


def _make_slicer_with_scoped_key(app):
    """Slicer whose account has a scoped key configured — needed for poll tests."""
    with app.app_context():
        acct = UplynkAccount(
            label="Acct",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("legacy-key"),
        )
        acct.scoped_kid = "test-kid"
        acct.scoped_sub = "test-sub"
        acct.scoped_private_b64_encrypted = encrypt("dummy-private-b64")
        acct.scoped_scp = "video.services.slicer.cloudslicer.live:read"
        db.session.add(acct)
        db.session.flush()
        slicer = Slicer(
            uplynk_account_id=acct.id,
            slicer_id="s1",
            slicer_api_url="https://ingest.example.com/s1",
            is_active=True,
            last_state="Stopped",
            protocol="SRT",
        )
        db.session.add(slicer)
        db.session.commit()
        return slicer.id


def _fresh(slicer_id="s1", state="Slicing", connection_mode="pull"):
    return DiscoveredSlicer(
        slicer_id=slicer_id,
        slicer_api_url=f"https://ingest.example.com/{slicer_id}",
        region="us-east-1",
        protocol="SRT",
        plugin_id=None,
        plugin_version=None,
        state=state,
        description=None,
        connection_mode=connection_mode,
    )


def test_state_requires_login(app, client):
    slicer_id = _make_slicer_with_scoped_key(app)
    response = client.get(f"/slicers/{slicer_id}/state", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_state_returns_badge_fragment(app, client):
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.retrieve_slicer",
        return_value=_fresh(state="AdBreak", connection_mode="pull"),
    ):
        response = client.get(f"/slicers/{slicer_id}/state")
    assert response.status_code == 200
    # Badge shows the fresh state
    assert b"AdBreak" in response.data
    assert b"badge--state--adbreak" in response.data
    # OOB meta swap includes push/pull qualifier
    assert b"SRT pull" in response.data
    assert b'hx-swap-oob="true"' in response.data


def test_state_persists_fresh_data(app, client):
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.retrieve_slicer",
        return_value=_fresh(state="Slicing", connection_mode="push"),
    ):
        client.get(f"/slicers/{slicer_id}/state")
    with app.app_context():
        slicer = db.session.get(Slicer, slicer_id)
        assert slicer.last_state == "Slicing"
        assert slicer.connection_mode == "push"
        assert slicer.last_seen_at is not None


def test_state_regular_user_denied_on_unassigned_slicer(app, client):
    slicer_id = _make_slicer_with_scoped_key(app)
    with app.app_context():
        u = User(
            email="ru@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.commit()
    _login(client, "ru@example.com", "password123")
    response = client.get(f"/slicers/{slicer_id}/state")
    assert response.status_code == 403


def test_state_unknown_slicer_returns_404(app, client):
    _login(client)
    response = client.get("/slicers/99999/state")
    assert response.status_code == 404


def test_state_uplynk_error_serves_stale_row(app, client):
    """When Uplynk hiccups, we render the last-known state rather than 500."""
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.retrieve_slicer",
        side_effect=UplynkAPIError("boom"),
    ):
        response = client.get(f"/slicers/{slicer_id}/state")
    assert response.status_code == 200
    # Whatever we had at row creation ("Stopped") should render
    assert b"Stopped" in response.data


def test_state_without_scoped_key_serves_stale_row(app, client):
    """If the account never had a scoped key, state route degrades gracefully."""
    _login(client)
    # Build a slicer whose account has NO scoped key
    slicer_id = _make_slicer(app)
    with app.app_context():
        s = db.session.get(Slicer, slicer_id)
        s.last_state = "Slicing"
        db.session.commit()
    response = client.get(f"/slicers/{slicer_id}/state")
    assert response.status_code == 200
    assert b"Slicing" in response.data
