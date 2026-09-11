"""Tests for the slicer control HTTP routes (HTMX endpoints)."""

from __future__ import annotations

from unittest.mock import patch

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.uplynk.csl import CSLResult


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
