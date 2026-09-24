"""Tests for the slicer control HTTP routes (HTMX endpoints)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

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
    # Buttons present for all six methods (four SHA1 + two v4 target-state)
    for method in ["status", "state", "content_start", "blackout", "start", "stop"]:
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


def _fresh(slicer_id="s1", state="Slicing", connection_mode="pull", thumb_url=None):
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
        thumb_url=thumb_url,
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


def test_state_includes_hidden_oob_thumb_when_no_thumb_url(app, client):
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.retrieve_slicer",
        return_value=_fresh(state="Stopped", thumb_url=None),
    ):
        response = client.get(f"/slicers/{slicer_id}/state")
    assert response.status_code == 200
    assert f'id="thumb-{slicer_id}"'.encode() in response.data
    assert b"hidden" in response.data


def test_state_includes_visible_oob_thumb_when_thumb_url_present(app, client):
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.retrieve_slicer",
        return_value=_fresh(state="Slicing", thumb_url="http://cdn.example.com/frame.jpg"),
    ):
        response = client.get(f"/slicers/{slicer_id}/state")
    assert response.status_code == 200
    assert f"/slicers/{slicer_id}/thumb?v=".encode() in response.data


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


# ---------------------------------------------------------------------------
# Route dispatch — start/stop methods route to set_target_state, not control_slicer
# ---------------------------------------------------------------------------


def test_control_route_dispatches_start_to_target_state(app, client):
    """method=start hits set_target_state, not control_slicer."""
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)

    with (
        patch("app.slicers.routes.set_target_state") as mock_target,
        patch("app.slicers.routes.control_slicer") as mock_control,
    ):
        from app.slicers.service import ControlOutcome

        mock_target.return_value = ControlOutcome(
            result=CSLResult(status_code=200, body={"target_state": "Ready"}, ok=True),
        )
        response = client.post(
            f"/slicers/{slicer_id}/control",
            data={"method": "start"},
        )

    assert response.status_code == 200
    mock_target.assert_called_once()
    mock_control.assert_not_called()
    assert mock_target.call_args.args[2] == "start"


def test_control_route_dispatches_stop_to_target_state(app, client):
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)

    with (
        patch("app.slicers.routes.set_target_state") as mock_target,
        patch("app.slicers.routes.control_slicer") as mock_control,
    ):
        from app.slicers.service import ControlOutcome

        mock_target.return_value = ControlOutcome(
            result=CSLResult(status_code=200, body={"target_state": "Stopped"}, ok=True),
        )
        client.post(
            f"/slicers/{slicer_id}/control",
            data={"method": "stop"},
        )

    mock_target.assert_called_once()
    mock_control.assert_not_called()
    assert mock_target.call_args.args[2] == "stop"


def test_control_route_still_dispatches_status_to_control_slicer(app, client):
    """SHA1 methods still route to control_slicer — dispatch is backward-compatible."""
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)

    with (
        patch("app.slicers.routes.control_slicer") as mock_control,
        patch("app.slicers.routes.set_target_state") as mock_target,
    ):
        from app.slicers.service import ControlOutcome

        mock_control.return_value = ControlOutcome(
            result=CSLResult(status_code=200, body={"state": "Capture"}, ok=True),
        )
        client.post(
            f"/slicers/{slicer_id}/control",
            data={"method": "status"},
        )

    mock_control.assert_called_once()
    mock_target.assert_not_called()


# --- Helpers for the coalesced-poll route ---


def _make_account_with_slicers(app, n=3):
    """Account with a scoped key + N slicers. Returns (account_id, slicer_ids)."""
    with app.app_context():
        acct = UplynkAccount(
            label="Acct",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("legacy-key"),
        )
        acct.scoped_kid = "test-kid"
        acct.scoped_sub = "test-sub"
        acct.scoped_private_b64_encrypted = encrypt("dummy-private-b64")
        acct.scoped_scp = "video.services.ingest.cloudslicer.live:read"
        db.session.add(acct)
        db.session.flush()

        slicer_ids = []
        for i in range(n):
            s = Slicer(
                uplynk_account_id=acct.id,
                slicer_id=f"s{i + 1}",
                slicer_api_url=f"https://ingest.example.com/s{i + 1}",
                is_active=True,
                last_state="Stopped",
                protocol="SRT",
            )
            db.session.add(s)
            db.session.flush()
            slicer_ids.append(s.id)
        db.session.commit()
        return acct.id, slicer_ids


# --- Tests for GET /slicers/accounts/<id>/slicer-states ---


def test_account_states_requires_login(app, client):
    account_id, _ = _make_account_with_slicers(app)
    response = client.get(
        f"/slicers/accounts/{account_id}/slicer-states",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_account_states_admin_gets_all_slicers(app, client):
    _login(client)  # bootstrap admin
    account_id, _ = _make_account_with_slicers(app, n=3)
    fresh_list = [
        DiscoveredSlicer(
            slicer_id=f"s{i + 1}",
            slicer_api_url=f"https://ingest.example.com/s{i + 1}",
            region="us-east-1",
            protocol="SRT",
            plugin_id=None,
            plugin_version=None,
            state="Slicing",
            description=None,
            connection_mode="pull",
        )
        for i in range(3)
    ]
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.list_slicers",
        return_value=fresh_list,
    ):
        response = client.get(f"/slicers/accounts/{account_id}/slicer-states")

    assert response.status_code == 200
    body = response.data
    # All three badges present with fresh state
    assert body.count(b'hx-swap-oob="true"') >= 6  # 3 badges + 3 meta rows
    assert b'id="badge-1"' in body
    assert b'id="badge-2"' in body
    assert b'id="badge-3"' in body
    assert body.count(b"Slicing") >= 3
    assert b"SRT pull" in body


def test_account_states_regular_user_gets_assigned_subset_only(app, client):
    account_id, slicer_ids = _make_account_with_slicers(app, n=3)
    # Create a regular user assigned to only s1 and s2 (not s3).
    with app.app_context():
        u = User(
            email="ru@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.flush()
        for sid in slicer_ids[:2]:
            u.slicers.append(db.session.get(Slicer, sid))
        db.session.commit()

    _login(client, "ru@example.com", "password123")

    fresh_list = [
        DiscoveredSlicer(
            slicer_id=f"s{i + 1}",
            slicer_api_url=f"https://ingest.example.com/s{i + 1}",
            region="us-east-1",
            protocol="SRT",
            plugin_id=None,
            plugin_version=None,
            state="Slicing",
            description=None,
            connection_mode="pull",
        )
        for i in range(3)
    ]
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.list_slicers",
        return_value=fresh_list,
    ):
        response = client.get(f"/slicers/accounts/{account_id}/slicer-states")

    assert response.status_code == 200
    body = response.data
    # Only s1 and s2 rendered; s3 fragments absent.
    assert f'id="badge-{slicer_ids[0]}"'.encode() in body
    assert f'id="badge-{slicer_ids[1]}"'.encode() in body
    assert f'id="badge-{slicer_ids[2]}"'.encode() not in body


def test_account_states_regular_user_with_no_assignments_denied(app, client):
    account_id, _ = _make_account_with_slicers(app, n=3)
    with app.app_context():
        u = User(
            email="unassigned@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.commit()

    _login(client, "unassigned@example.com", "password123")
    response = client.get(f"/slicers/accounts/{account_id}/slicer-states")
    assert response.status_code == 403


def test_account_states_unknown_account_returns_404(app, client):
    _login(client)
    response = client.get("/slicers/accounts/99999/slicer-states")
    assert response.status_code == 404


def test_account_states_uplynk_error_serves_stale_rows(app, client):
    """When Uplynk hiccups, render last-known state per slicer rather than 500."""
    _login(client)
    account_id, _ = _make_account_with_slicers(app, n=3)
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.list_slicers",
        side_effect=UplynkAPIError("boom"),
    ):
        response = client.get(f"/slicers/accounts/{account_id}/slicer-states")

    assert response.status_code == 200
    # All three stale rows still render with their last-known state.
    assert response.data.count(b"Stopped") >= 3
    assert response.data.count(b'hx-swap-oob="true"') >= 6


def test_account_states_makes_single_uplynk_call(app, client):
    """One list_slicers() call per poll, regardless of slicer count."""
    _login(client)
    account_id, _ = _make_account_with_slicers(app, n=5)
    fresh_list = [
        DiscoveredSlicer(
            slicer_id=f"s{i + 1}",
            slicer_api_url=f"https://ingest.example.com/s{i + 1}",
            region="us-east-1",
            protocol="SRT",
            plugin_id=None,
            plugin_version=None,
            state="Slicing",
            description=None,
            connection_mode="pull",
        )
        for i in range(5)
    ]
    with patch(
        "app.uplynk.discovery.UplynkDiscoveryClient.list_slicers",
        return_value=fresh_list,
    ) as mock_list:
        client.get(f"/slicers/accounts/{account_id}/slicer-states")

    assert mock_list.call_count == 1


# ---------------------------------------------------------------------------
# Thumbnail proxy route (see #28)
# ---------------------------------------------------------------------------


def _make_slicer_with_thumb(app, thumb_url="http://cdn.example.com/frame.jpg"):
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
            thumb_url=thumb_url,
        )
        db.session.add(slicer)
        db.session.commit()
        return slicer.id


def _mock_upstream_image(status_code=200, content=b"\xff\xd8\xff", content_type="image/jpeg"):
    mock = MagicMock()
    mock.status_code = status_code
    mock.content = content
    mock.headers = {"Content-Type": content_type}
    mock.raise_for_status = MagicMock()
    return mock


def test_thumb_requires_login(app, client):
    slicer_id = _make_slicer_with_thumb(app)
    response = client.get(f"/slicers/{slicer_id}/thumb", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_thumb_unknown_slicer_returns_404(app, client):
    _login(client)
    response = client.get("/slicers/99999/thumb")
    assert response.status_code == 404


def test_thumb_slicer_without_thumb_url_returns_404(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    response = client.get(f"/slicers/{slicer_id}/thumb")
    assert response.status_code == 404


def test_thumb_regular_user_denied_on_unassigned_slicer(app, client):
    slicer_id = _make_slicer_with_thumb(app)
    with app.app_context():
        u = User(
            email="ru@example.com",
            password_hash=hash_password("password123"),
        )
        db.session.add(u)
        db.session.commit()
    _login(client, "ru@example.com", "password123")
    response = client.get(f"/slicers/{slicer_id}/thumb")
    assert response.status_code == 403


def test_thumb_proxies_upstream_bytes(app, client):
    _login(client)
    slicer_id = _make_slicer_with_thumb(app, thumb_url="http://cdn.example.com/frame.jpg")
    upstream = _mock_upstream_image(content=b"fake-jpeg-bytes", content_type="image/jpeg")
    with patch("app.slicers.routes.requests.get", return_value=upstream) as mock_get:
        response = client.get(f"/slicers/{slicer_id}/thumb")

    mock_get.assert_called_once_with("http://cdn.example.com/frame.jpg", timeout=5)
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    assert response.data == b"fake-jpeg-bytes"


def test_thumb_upstream_failure_returns_502(app, client):
    _login(client)
    slicer_id = _make_slicer_with_thumb(app)
    with patch("app.slicers.routes.requests.get", side_effect=requests.RequestException("boom")):
        response = client.get(f"/slicers/{slicer_id}/thumb")
    assert response.status_code == 502


def test_thumb_upstream_non_2xx_returns_502(app, client):
    _login(client)
    slicer_id = _make_slicer_with_thumb(app)
    upstream = _mock_upstream_image(status_code=404)
    upstream.raise_for_status.side_effect = requests.HTTPError("404")
    with patch("app.slicers.routes.requests.get", return_value=upstream):
        response = client.get(f"/slicers/{slicer_id}/thumb")
    assert response.status_code == 502
