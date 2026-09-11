"""Tests for admin Uplynk account CRUD."""

from __future__ import annotations

from app.crypto import decrypt
from app.models import UplynkAccount, User, db


def _login(client, email="test@example.com", password="test-password-123"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password, "submit": "Sign in"},
        follow_redirects=True,
    )


def _make_regular_user(app):
    from app.auth.passwords import hash_password
    with app.app_context():
        u = User(
            email="user@example.com",
            password_hash=hash_password("user-password"),
            is_admin=False,
        )
        db.session.add(u)
        db.session.commit()


def test_non_admin_cannot_access(app, client):
    _make_regular_user(app)
    _login(client, "user@example.com", "user-password")
    response = client.get("/admin/uplynk-accounts")
    assert response.status_code == 403


def test_anonymous_redirected_to_login(client):
    response = client.get("/admin/uplynk-accounts", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_admin_sees_empty_list(app, client):
    _login(client)
    response = client.get("/admin/uplynk-accounts")
    assert response.status_code == 200
    assert b"No Uplynk accounts yet" in response.data


def test_admin_creates_account(app, client):
    _login(client)
    response = client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "Prod",
            "workspace_id": "workspace-abc",
            "legacy_api_key": "Vk1234567890abcdef",
            "scoped_api_key": "scoped-key-xyz",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        acct = db.session.query(UplynkAccount).filter_by(label="Prod").one()
        assert acct.workspace_id == "workspace-abc"
        assert decrypt(acct.legacy_api_key_encrypted) == "Vk1234567890abcdef"
        assert decrypt(acct.scoped_api_key_encrypted) == "scoped-key-xyz"


def test_create_requires_legacy_key(app, client):
    _login(client)
    response = client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "NoKey",
            "workspace_id": "ws-1",
            "legacy_api_key": "",
            "scoped_api_key": "",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    assert b"Legacy API Key is required" in response.data
    with app.app_context():
        assert db.session.query(UplynkAccount).filter_by(label="NoKey").count() == 0


def test_admin_edits_label_without_changing_keys(app, client):
    _login(client)
    # Create first
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "Original",
            "workspace_id": "ws-1",
            "legacy_api_key": "original-key",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="Original").one().id
        original_cipher = db.session.get(UplynkAccount, acct_id).legacy_api_key_encrypted

    # Edit label only (blank key fields = keep existing)
    client.post(
        f"/admin/uplynk-accounts/{acct_id}/edit",
        data={
            "label": "Renamed",
            "workspace_id": "ws-1",
            "legacy_api_key": "",
            "scoped_api_key": "",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        assert acct.label == "Renamed"
        assert acct.legacy_api_key_encrypted == original_cipher
        assert decrypt(acct.legacy_api_key_encrypted) == "original-key"


def test_admin_deletes_account(app, client):
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "ToDelete",
            "workspace_id": "ws-1",
            "legacy_api_key": "some-key",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="ToDelete").one().id

    client.post(
        f"/admin/uplynk-accounts/{acct_id}/delete",
        follow_redirects=True,
    )
    with app.app_context():
        assert db.session.get(UplynkAccount, acct_id) is None
