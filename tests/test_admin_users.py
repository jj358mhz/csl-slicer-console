"""Tests for admin user management + slicer assignment."""

from __future__ import annotations

from app.auth.passwords import hash_password, verify_password
from app.crypto import encrypt
from app.models import Slicer, UplynkAccount, User, db


def _login(client, email="test@example.com", password="test-password-123"):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password, "submit": "Sign in"},
        follow_redirects=True,
    )


def _make_slicer(app, slicer_id="s1"):
    with app.app_context():
        acct = UplynkAccount(
            label=f"Acct-{slicer_id}",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("k"),
        )
        db.session.add(acct)
        db.session.flush()
        slicer = Slicer(
            uplynk_account_id=acct.id,
            slicer_id=slicer_id,
            slicer_api_url=f"https://example.com/{slicer_id}",
            is_active=True,
        )
        db.session.add(slicer)
        db.session.commit()
        return slicer.id


def test_admin_lists_users(app, client):
    _login(client)
    response = client.get("/admin/users")
    assert response.status_code == 200
    assert b"test@example.com" in response.data


def test_admin_creates_user(app, client):
    _login(client)
    response = client.post(
        "/admin/users/new",
        data={
            "email": "newuser@example.com",
            "password": "safe-password-123",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        u = db.session.query(User).filter_by(email="newuser@example.com").one()
        assert u.is_admin is False
        assert verify_password(u.password_hash, "safe-password-123")


def test_admin_cannot_duplicate_email(app, client):
    _login(client)
    client.post(
        "/admin/users/new",
        data={
            "email": "dup@example.com",
            "password": "safe-password-123",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    response = client.post(
        "/admin/users/new",
        data={
            "email": "dup@example.com",
            "password": "another-password",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    assert b"already exists" in response.data


def test_edit_leaves_password_unchanged_when_blank(app, client):
    _login(client)
    client.post(
        "/admin/users/new",
        data={
            "email": "victor@example.com",
            "password": "original-password",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        u = db.session.query(User).filter_by(email="victor@example.com").one()
        original_hash = u.password_hash
        uid = u.id

    client.post(
        f"/admin/users/{uid}/edit",
        data={
            "email": "victor@example.com",
            "password": "",
            "is_active": "y",
            "submit": "Save",
        },
        follow_redirects=True,
    )
    with app.app_context():
        u = db.session.get(User, uid)
        assert u.password_hash == original_hash


def test_admin_cannot_demote_self(app, client):
    _login(client)
    with app.app_context():
        me = db.session.query(User).filter_by(email="test@example.com").one()
        my_id = me.id

    response = client.post(
        f"/admin/users/{my_id}/edit",
        data={
            "email": "test@example.com",
            "is_active": "y",
            # is_admin unchecked
            "submit": "Save",
        },
        follow_redirects=True,
    )
    assert b"remove your own admin status" in response.data
    with app.app_context():
        me = db.session.get(User, my_id)
        assert me.is_admin is True


def test_admin_cannot_delete_self(app, client):
    _login(client)
    with app.app_context():
        my_id = db.session.query(User).filter_by(email="test@example.com").one().id

    client.post(f"/admin/users/{my_id}/delete", follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, my_id) is not None


def test_assign_slicers_to_user(app, client):
    _login(client)
    s1_id = _make_slicer(app, "s1")
    s2_id = _make_slicer(app, "s2")

    with app.app_context():
        target = User(
            email="target@example.com",
            password_hash=hash_password("pass1234-x"),
        )
        db.session.add(target)
        db.session.commit()
        target_id = target.id

    client.post(
        f"/admin/users/{target_id}/slicers",
        data={"slicer_ids": [s1_id, s2_id], "submit": "Save"},
        follow_redirects=True,
    )
    with app.app_context():
        u = db.session.get(User, target_id)
        assigned = sorted(s.slicer_id for s in u.slicers)
        assert assigned == ["s1", "s2"]


def test_user_dashboard_shows_only_assigned_slicers(app, client):
    # Admin creates a slicer and a regular user with one assignment
    _login(client)
    s1_id = _make_slicer(app, "assigned-slicer")
    _make_slicer(app, "unassigned-slicer")

    with app.app_context():
        u = User(
            email="regular@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.commit()
        u_id = u.id

    client.post(
        f"/admin/users/{u_id}/slicers",
        data={"slicer_ids": [s1_id], "submit": "Save"},
        follow_redirects=True,
    )

    # Log out, log in as regular user
    client.post("/auth/logout")
    _login(client, "regular@example.com", "password123")

    response = client.get("/")
    assert response.status_code == 200
    assert b"assigned-slicer" in response.data
    assert b"unassigned-slicer" not in response.data
