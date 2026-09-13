"""Tests for admin event logging."""

from __future__ import annotations

from unittest.mock import patch

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AdminEvent, UplynkAccount, User, db


def _login(client, email="test@example.com", password="test-password-123"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password, "submit": "Sign in"},
        follow_redirects=True,
    )


def test_creating_user_logs_event(app, client):
    _login(client)
    client.post(
        "/admin/users/new",
        data={
            "email": "logged@example.com",
            "password": "safe-password-123",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        event = db.session.query(AdminEvent).filter_by(category="user", action="create").one()
        assert "logged@example.com" in event.summary
        assert event.target == "logged@example.com"


def test_deleting_user_logs_event(app, client):
    _login(client)
    with app.app_context():
        u = User(
            email="doomed@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.commit()
        uid = u.id

    client.post(f"/admin/users/{uid}/delete", follow_redirects=True)
    with app.app_context():
        assert (
            db.session.query(AdminEvent)
            .filter_by(category="user", action="delete", target="doomed@example.com")
            .count()
            == 1
        )


def test_creating_uplynk_account_logs_event(app, client):
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "loggedacct",
            "workspace_id": "ws",
            "legacy_api_key": "somekey",
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        assert (
            db.session.query(AdminEvent)
            .filter_by(category="uplynk_account", action="create", target="loggedacct")
            .count()
            == 1
        )


def test_sync_success_logs_event(app, client):
    from app.uplynk.discovery import DiscoveredSlicer

    _login(client)
    with app.app_context():
        acct = UplynkAccount(
            label="synclogtest",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("k"),
            scoped_kid="k",
            scoped_sub="s",
            scoped_private_b64_encrypted=encrypt("b"),
            scoped_scp="x",
        )
        db.session.add(acct)
        db.session.commit()
        acct_id = acct.id

    with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[
                DiscoveredSlicer(
                    slicer_id="s1",
                    slicer_api_url="https://example.com/s1",
                    region=None,
                    protocol=None,
                    plugin_id=None,
                    plugin_version=None,
                    state=None,
                    description=None,
                    connection_mode=None,
                )
            ],
    ):
        client.post(f"/admin/uplynk-accounts/{acct_id}/sync", follow_redirects=True)

    with app.app_context():
        event = (
            db.session.query(AdminEvent)
            .filter_by(category="sync", action="run", target="synclogtest")
            .one()
        )
        assert "1 new" in event.summary


def test_event_records_actor(app, client):
    _login(client)
    client.post(
        "/admin/users/new",
        data={
            "email": "who@example.com",
            "password": "password1234",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        admin = db.session.query(User).filter_by(email="test@example.com").one()
        event = db.session.query(AdminEvent).filter_by(category="user", action="create").one()
        assert event.actor_id == admin.id
