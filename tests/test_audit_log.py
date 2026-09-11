"""Tests for the audit log views (admin + per-user history)."""

from __future__ import annotations

from unittest.mock import patch

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.uplynk.csl import CSLResult


def _login(client, email="test@example.com", password="test-password-123"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password, "submit": "Sign in"},
        follow_redirects=True,
    )


def _seed(app):
    """Create an admin, a regular user, a slicer, and two audit events."""
    with app.app_context():
        acct = UplynkAccount(
            label="Acct",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("k"),
        )
        db.session.add(acct)
        db.session.flush()
        slicer = Slicer(
            uplynk_account_id=acct.id,
            slicer_id="s1",
            slicer_api_url="https://example.com/s1",
            is_active=True,
        )
        regular = User(
            email="ru@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add_all([slicer, regular])
        db.session.commit()
        return {"slicer_id": slicer.id, "regular_id": regular.id}


def _trigger_actions(client, slicer_id):
    """Fire two real (mocked) control calls so audit events land."""
    with patch(
        "app.slicers.service.call_slicer",
        return_value=CSLResult(200, {"state": "Running"}, True),
    ):
        client.post(f"/slicers/{slicer_id}/control", data={"method": "status"})
        client.post(f"/slicers/{slicer_id}/control", data={"method": "state"})


def test_admin_sees_audit_log(app, client):
    ids = _seed(app)
    _login(client)
    _trigger_actions(client, ids["slicer_id"])

    response = client.get("/admin/audit")
    assert response.status_code == 200
    assert b"status" in response.data
    assert b"state" in response.data
    assert b"test@example.com" in response.data


def test_non_admin_cannot_see_audit_log(app, client):
    _seed(app)
    _login(client, "ru@example.com", "password123")
    response = client.get("/admin/audit")
    assert response.status_code == 403


def test_audit_log_filters_by_method(app, client):
    ids = _seed(app)
    _login(client)
    _trigger_actions(client, ids["slicer_id"])

    # Filter to just 'status'
    response = client.get("/admin/audit?method=status")
    assert response.status_code == 200
    assert b"1 event" in response.data


def test_audit_log_filters_by_user(app, client):
    ids = _seed(app)
    _login(client)
    _trigger_actions(client, ids["slicer_id"])

    with app.app_context():
        admin_id = db.session.query(User).filter_by(email="test@example.com").one().id

    response = client.get(f"/admin/audit?user_id={admin_id}")
    assert response.status_code == 200
    assert b"2 events" in response.data


def test_user_history_shows_only_own_events(app, client):
    ids = _seed(app)

    # Admin triggers one event
    _login(client)
    _trigger_actions(client, ids["slicer_id"])
    client.post("/auth/logout")

    # Regular user (assigned via direct DB insert to avoid CSRF weirdness)
    with app.app_context():
        regular = db.session.get(User, ids["regular_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        regular.slicers.append(slicer)
        db.session.commit()

    _login(client, "ru@example.com", "password123")
    with patch(
        "app.slicers.service.call_slicer",
        return_value=CSLResult(200, {"state": "R"}, True),
    ):
        client.post(f"/slicers/{ids['slicer_id']}/control", data={"method": "status"})

    response = client.get("/history")
    assert response.status_code == 200
    with app.app_context():
        # 3 total events, but this user's history should show only 1
        assert db.session.query(AuditEvent).count() == 3
    # Count '<tr>' occurrences in the body to check row count (header + 1 data row)
    assert response.data.count(b"<tr>") == 2


def test_history_requires_login(client):
    response = client.get("/history", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
