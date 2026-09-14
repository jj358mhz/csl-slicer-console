"""Tests for admin Uplynk account CRUD."""

from __future__ import annotations

import io

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


def _scoped_env_bytes(
    kid="k-1", sub="s-1", scp="video.services.ingest.slicer.cloudslicer.live:read"
):
    return (f"KID={kid}\nSUB={sub}\nPRIVATE_B64=aGVsbG8=\nSCP={scp}\n").encode()


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


def test_admin_creates_account_without_scoped_key(app, client):
    _login(client)
    response = client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "Prod",
            "workspace_id": "workspace-abc",
            "legacy_api_key": "Vk1234567890abcdef",
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        acct = db.session.query(UplynkAccount).filter_by(label="Prod").one()
        assert decrypt(acct.legacy_api_key_encrypted) == "Vk1234567890abcdef"
        assert acct.has_scoped_key is False


def test_admin_creates_account_with_scoped_env(app, client):
    _login(client)
    response = client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "Prod",
            "workspace_id": "workspace-abc",
            "legacy_api_key": "Vk1234567890abcdef",
            "scoped_env_file": (io.BytesIO(_scoped_env_bytes()), "key.env"),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        acct = db.session.query(UplynkAccount).filter_by(label="Prod").one()
        assert acct.has_scoped_key is True
        assert acct.scoped_kid == "k-1"
        assert acct.scoped_sub == "s-1"
        assert decrypt(acct.scoped_private_b64_encrypted) == "aGVsbG8="


def test_create_requires_legacy_key(app, client):
    _login(client)
    response = client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "NoKey",
            "workspace_id": "ws-1",
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Legacy API Key is required" in response.data


def test_admin_edits_label_without_changing_keys(app, client):
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "Original",
            "workspace_id": "ws-1",
            "legacy_api_key": "original-key",
            "scoped_env_file": (io.BytesIO(_scoped_env_bytes(kid="orig-kid")), "orig.env"),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct = db.session.query(UplynkAccount).filter_by(label="Original").one()
        acct_id = acct.id
        original_cipher = acct.legacy_api_key_encrypted
        original_kid = acct.scoped_kid

    client.post(
        f"/admin/uplynk-accounts/{acct_id}/edit",
        data={
            "label": "Renamed",
            "workspace_id": "ws-1",
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        assert acct.label == "Renamed"
        assert acct.legacy_api_key_encrypted == original_cipher
        assert acct.scoped_kid == original_kid  # unchanged


def test_admin_replaces_scoped_env(app, client):
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "Acct",
            "workspace_id": "ws-1",
            "legacy_api_key": "legacy-key",
            "scoped_env_file": (io.BytesIO(_scoped_env_bytes(kid="old-kid")), "old.env"),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="Acct").one().id

    # Upload replacement
    client.post(
        f"/admin/uplynk-accounts/{acct_id}/edit",
        data={
            "label": "Acct",
            "workspace_id": "ws-1",
            "scoped_env_file": (io.BytesIO(_scoped_env_bytes(kid="new-kid")), "new.env"),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        assert acct.scoped_kid == "new-kid"


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
        content_type="multipart/form-data",
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


# ---------------------------------------------------------------------------
# Scoped API Key permissions display (issue #14)
# ---------------------------------------------------------------------------


def test_edit_page_renders_scope_badges(app, client):
    """The edit page renders each stored scope as its own badge."""
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "ScopedAcct",
            "workspace_id": "ws-1",
            "legacy_api_key": "legacy-key",
            "scoped_env_file": (
                io.BytesIO(
                    _scoped_env_bytes(
                        scp=(
                            "video.services.ingest.slicer.cloudslicer.live:read,"
                            "video.services.ingest.slicer.cloudslicer.live:write"
                        )
                    )
                ),
                "key.env",
            ),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="ScopedAcct").one().id

    response = client.get(f"/admin/uplynk-accounts/{acct_id}/edit")
    assert response.status_code == 200
    body = response.data.decode()
    assert 'data-testid="scoped-key-scopes"' in body
    assert "video.services.ingest.slicer.cloudslicer.live:read" in body
    assert "video.services.ingest.slicer.cloudslicer.live:write" in body
    # Both scopes should be inside individual badge elements
    assert body.count('class="badge badge--scope"') == 2


def test_edit_page_shows_empty_state_when_no_scoped_key(app, client):
    """An account with no scoped key shows the empty-state message."""
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "NoScope",
            "workspace_id": "ws-1",
            "legacy_api_key": "legacy-only",
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="NoScope").one().id

    response = client.get(f"/admin/uplynk-accounts/{acct_id}/edit")
    assert response.status_code == 200
    body = response.data.decode()
    assert 'data-testid="scoped-key-scopes"' in body
    assert "No scoped key configured" in body
    assert 'class="badge badge--scope"' not in body


def test_known_scope_has_tooltip(app, client):
    """A scope in the description map gets a title attribute; unknown scopes don't."""
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "TooltipAcct",
            "workspace_id": "ws-1",
            "legacy_api_key": "legacy-key",
            "scoped_env_file": (
                io.BytesIO(
                    _scoped_env_bytes(
                        scp=(
                            "video.services.ingest.slicer.cloudslicer.live:read,"
                            "video.services.unknown.made.up:read"
                        )
                    )
                ),
                "key.env",
            ),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="TooltipAcct").one().id

    response = client.get(f"/admin/uplynk-accounts/{acct_id}/edit")
    body = response.data.decode()
    # Known scope has a tooltip
    assert 'title="Read CSL (Cloud Slicer Live) slicer state and metadata."' in body
    # Unknown scope is still rendered but without a title attribute
    assert "video.services.unknown.made.up:read" in body


def test_scope_list_ignores_whitespace_and_empty_entries(app, client):
    """Malformed scope strings (extra commas, whitespace) don't produce empty badges."""
    _login(client)
    client.post(
        "/admin/uplynk-accounts/new",
        data={
            "label": "MessyAcct",
            "workspace_id": "ws-1",
            "legacy_api_key": "legacy-key",
            "scoped_env_file": (
                io.BytesIO(
                    _scoped_env_bytes(
                        scp=(
                            " video.services.ingest.slicer.cloudslicer.live:read ,,"
                            " video.services.ingest.slicer.cloudslicer.live:write "
                        )
                    )
                ),
                "messy.env",
            ),
            "submit": "Save",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        acct_id = db.session.query(UplynkAccount).filter_by(label="MessyAcct").one().id

    response = client.get(f"/admin/uplynk-accounts/{acct_id}/edit")
    body = response.data.decode()
    # Exactly two badges rendered — the empty entry between the commas is skipped
    assert body.count('class="badge badge--scope"') == 2
