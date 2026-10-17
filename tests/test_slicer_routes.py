"""Tests for the slicer control HTTP routes (HTMX endpoints)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.uplynk.csl import CSLResult
from app.uplynk.discovery import DiscoveredSlicer, UplynkAPIError


@pytest.fixture(autouse=True)
def _clear_thumb_cache():
    """The thumb proxy's in-process cache is module-level state, shared
    across tests unless cleared — different tests can otherwise reuse the
    same slicer id + thumb_url and see a stale cached response."""
    from app.slicers.routes import _THUMB_CACHE

    _THUMB_CACHE.clear()
    yield
    _THUMB_CACHE.clear()


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


def test_dashboard_marks_state_changing_actions_for_confirmation(app, client):
    _login(client)
    _make_slicer(app)
    response = client.get("/")
    assert response.status_code == 200
    # Start / Stop / Content Start / Blackout are gated behind the confirm
    # modal, both on the slicer tile and in the batch bar (one of each).
    for method in ["content_start", "blackout", "start", "stop"]:
        assert response.data.count(f'data-confirm-method="{method}"'.encode()) == 2
    # Read-only methods fire without a prompt.
    for method in ["status", "state"]:
        assert f'data-confirm-method="{method}"'.encode() not in response.data
    assert b'id="confirm-modal"' in response.data


def _set_last_state(app, slicer_id, state):
    with app.app_context():
        db.session.get(Slicer, slicer_id).last_state = state
        db.session.commit()


@pytest.mark.parametrize("state", ["Initializing", "Stopping"])
def test_transitional_badge_fast_polls_its_slicer(app, client, state):
    _login(client)
    slicer_id = _make_slicer(app)
    _set_last_state(app, slicer_id, state)
    response = client.get("/")
    assert f"badge--state--{state.lower()}".encode() in response.data
    assert f'hx-get="/slicers/{slicer_id}/state"'.encode() in response.data
    assert b'hx-trigger="every 1s"' in response.data


@pytest.mark.parametrize("state", ["Slicing", "Ready", "Blackout", "Stopped", None])
def test_non_initializing_badge_does_not_fast_poll(app, client, state):
    _login(client)
    slicer_id = _make_slicer(app)
    _set_last_state(app, slicer_id, state)
    response = client.get("/")
    assert f'hx-get="/slicers/{slicer_id}/state"'.encode() not in response.data
    assert b'hx-trigger="every 1s"' not in response.data


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


def test_thumb_second_request_within_ttl_uses_cache(app, client):
    """Concurrent viewers polling the same slicer shouldn't each trigger a
    fresh upstream fetch — see #28."""
    _login(client)
    slicer_id = _make_slicer_with_thumb(app)
    upstream = _mock_upstream_image(content=b"frame-a")
    with patch("app.slicers.routes.requests.get", return_value=upstream) as mock_get:
        r1 = client.get(f"/slicers/{slicer_id}/thumb")
        r2 = client.get(f"/slicers/{slicer_id}/thumb")

    assert mock_get.call_count == 1
    assert r1.data == b"frame-a"
    assert r2.data == b"frame-a"


def test_thumb_cache_bypassed_when_thumb_url_changes(app, client):
    """A genuinely new frame (new thumb_url) is never served from a stale cache."""
    _login(client)
    slicer_id = _make_slicer_with_thumb(app, thumb_url="http://cdn.example.com/a.jpg")
    upstream_a = _mock_upstream_image(content=b"frame-a")
    with patch("app.slicers.routes.requests.get", return_value=upstream_a):
        client.get(f"/slicers/{slicer_id}/thumb")

    with app.app_context():
        s = db.session.get(Slicer, slicer_id)
        s.thumb_url = "http://cdn.example.com/b.jpg"
        db.session.commit()

    upstream_b = _mock_upstream_image(content=b"frame-b")
    with patch("app.slicers.routes.requests.get", return_value=upstream_b) as mock_get:
        response = client.get(f"/slicers/{slicer_id}/thumb")

    mock_get.assert_called_once_with("http://cdn.example.com/b.jpg", timeout=5)
    assert response.data == b"frame-b"


def test_thumb_cache_expires_after_ttl(app, client):
    _login(client)
    slicer_id = _make_slicer_with_thumb(app)
    upstream = _mock_upstream_image(content=b"frame-a")
    with patch("app.slicers.routes.requests.get", return_value=upstream) as mock_get:
        client.get(f"/slicers/{slicer_id}/thumb")

    import app.slicers.routes as routes_mod

    with (
        patch.object(routes_mod.time, "monotonic", return_value=time.monotonic() + 999),
        patch("app.slicers.routes.requests.get", return_value=upstream) as mock_get_2,
    ):
        client.get(f"/slicers/{slicer_id}/thumb")

    assert mock_get.call_count == 1
    assert mock_get_2.call_count == 1


# --- Config panel (#49) ---


def _store_config(app, slicer_id, *, state="Slicing", error=None, source=None, **fields):
    from datetime import UTC, datetime

    from app.uplynk.slicer_config import SlicerConfig

    defaults = {
        "protocol": "SRT",
        "encoding_profile_id": "enc-profile-1",
        "streaming_url": "srt://ingest.example.com:7000",
        "passphrase": "s3cret-pass",
        "plugin_id": "tennis-scte35",
        "plugin_version": "1.0",
        "configuration": "latency: 2000\nurl: rtmp://host:1935/live\nfree text",
    }
    with app.app_context():
        s = db.session.get(Slicer, slicer_id)
        s.last_state = state
        s.config_encrypted = encrypt(SlicerConfig(**{**defaults, **fields}).to_json())
        s.config_captured_at = datetime(2026, 10, 16, 14, 2, 11, tzinfo=UTC)
        s.config_error = error
        s.config_source = source
        db.session.commit()


def _config_header_text(body):
    """The header's caption (left of the Refresh button), whitespace-collapsed."""
    header = body.split('class="config__header">', 1)[1]
    return " ".join(header.split("<span>", 1)[1].split("</span>", 1)[0].split())


def test_config_requires_login(app, client):
    slicer_id = _make_slicer(app)
    response = client.get(f"/slicers/{slicer_id}/config", follow_redirects=False)
    assert response.status_code == 302


def test_config_unknown_slicer_returns_404(app, client):
    _login(client)
    assert client.get("/slicers/99999/config").status_code == 404


def test_config_regular_user_denied_on_unassigned_slicer(app, client):
    slicer_id = _make_slicer(app)
    with app.app_context():
        db.session.add(User(email="ru@example.com", password_hash=hash_password("password123")))
        db.session.commit()
    _login(client, "ru@example.com", "password123")
    assert client.get(f"/slicers/{slicer_id}/config").status_code == 403


def test_config_renders_stored_capture_without_calling_uplynk(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id)
    with patch("app.uplynk.discovery.UplynkDiscoveryClient._retrieve") as mock_retrieve:
        response = client.get(f"/slicers/{slicer_id}/config")
    mock_retrieve.assert_not_called()
    assert response.status_code == 200
    body = response.data.decode()
    assert "Captured at start" in body
    assert 'datetime="2026-10-16T14:02:11Z"' in body
    assert "enc-profile-1" in body
    assert "tennis-scte35 · v1.0" in body
    assert "s3cret-pass" in body  # shown in plain text, per #49
    # k:v table — split on the first colon only; unparseable lines kept.
    assert '<th scope="row">latency</th>' in body
    assert "rtmp://host:1935/live" in body
    assert '<td colspan="2">free text</td>' in body


def test_config_marked_from_last_run_once_stopped(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id, state="Stopped")
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    assert "Captured at start" not in body
    # One separator only: "From last run · captured <time>" (#51).
    header = _config_header_text(body)
    assert header == (
        'From last run · captured <time class="local-time" '
        'datetime="2026-10-16T14:02:11Z">2026-10-16 14:02:11 UTC</time>'
    )


def test_config_header_while_running_has_one_separator(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id)
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    header = _config_header_text(body)
    assert header.startswith('Captured at start · <time class="local-time"')


def test_config_without_plugin_shows_none(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id, plugin_id=None, plugin_version=None)
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    assert "<dt>Plugin</dt>\n    <dd>none</dd>" in body


def test_config_not_captured_yet(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _set_last_state(app, slicer_id, "Stopped")
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    assert "Not captured yet" in body
    assert "next time this slicer starts" in body


def test_config_shows_capture_error_alongside_previous_capture(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id, error="JWT lacks required scope")
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    assert "showing the previous capture" in body
    assert "JWT lacks required scope" in body


def test_dashboard_renders_config_toggle_closed(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    body = client.get("/").data.decode()
    assert f'aria-controls="config-{slicer_id}"' in body
    assert f'data-config-url="/slicers/{slicer_id}/config"' in body
    assert f'<div id="config-{slicer_id}" class="slicer-card__config" hidden></div>' in body


def test_badge_carries_config_version(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id)
    body = client.get("/").data.decode()
    assert 'data-config-version="2026-10-16T14:02:11Z|1|"' in body


def test_config_sections_have_copy_buttons(app, client):
    import html
    import re

    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id, region="us-east-1", description='Court "1"')
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    copies = {
        title: html.unescape(text)
        for text, title in re.findall(r'data-copy-text="([^"]*)"\s+title="Copy (\w+)"', body)
    }
    assert set(copies) == {"Summary", "Connection", "Configuration"}
    assert copies["Summary"].splitlines()[:2] == ["Protocol: SRT", "Region: us-east-1"]
    assert 'Description: Court "1"' in copies["Summary"]
    assert "Passphrase: s3cret-pass" in copies["Connection"]
    # The raw configuration string, as Uplynk returned it.
    assert copies["Configuration"] == "latency: 2000\nurl: rtmp://host:1935/live\nfree text"


# --- Config refresh (#57) ---


def test_config_panel_has_refresh_button(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    body = client.get(f"/slicers/{slicer_id}/config").data.decode()
    assert f'hx-post="/slicers/{slicer_id}/config/refresh"' in body
    assert f'hx-target="#config-{slicer_id}"' in body


def test_config_refresh_requires_login(app, client):
    slicer_id = _make_slicer_with_scoped_key(app)
    assert client.post(f"/slicers/{slicer_id}/config/refresh").status_code == 302


def test_config_refresh_unknown_slicer_returns_404(app, client):
    _login(client)
    assert client.post("/slicers/99999/config/refresh").status_code == 404


def test_config_refresh_regular_user_denied_on_unassigned_slicer(app, client):
    slicer_id = _make_slicer_with_scoped_key(app)
    with app.app_context():
        db.session.add(User(email="ru@example.com", password_hash=hash_password("password123")))
        db.session.commit()
    _login(client, "ru@example.com", "password123")
    assert client.post(f"/slicers/{slicer_id}/config/refresh").status_code == 403


def test_config_refresh_fetches_and_renders_new_capture(app, client):
    from app.uplynk.slicer_config import SlicerConfig

    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    _store_config(app, slicer_id, plugin_version="1.0")
    fresh = SlicerConfig(protocol="SRT", plugin_id="tennis-scte35", plugin_version="2.0")
    with patch(
        "app.slicers.service.UplynkDiscoveryClient.retrieve_slicer_config",
        return_value=fresh,
    ) as mock_cfg:
        response = client.post(f"/slicers/{slicer_id}/config/refresh")
    mock_cfg.assert_called_once_with("s1")
    assert response.status_code == 200
    body = response.data.decode()
    assert "tennis-scte35 · v2.0" in body
    assert "v1.0" not in body
    # Subsequent opens serve the refreshed capture.
    assert "v2.0" in client.get(f"/slicers/{slicer_id}/config").data.decode()


def test_config_refresh_failure_shows_error_and_keeps_capture(app, client):
    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    _store_config(app, slicer_id)
    with patch(
        "app.slicers.service.UplynkDiscoveryClient.retrieve_slicer_config",
        side_effect=UplynkAPIError("Uplynk returned 503"),
    ):
        body = client.post(f"/slicers/{slicer_id}/config/refresh").data.decode()
    assert "showing the previous capture" in body
    assert "Uplynk returned 503" in body
    assert "tennis-scte35 · v1.0" in body


def test_config_refresh_without_scoped_key_shows_error(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id)
    body = client.post(f"/slicers/{slicer_id}/config/refresh").data.decode()
    assert "no scoped API key" in body
    assert "tennis-scte35 · v1.0" in body


@pytest.mark.parametrize(
    ("state", "source", "caption"),
    [
        ("Slicing", None, "Captured at start ·"),
        ("Slicing", "start", "Captured at start ·"),
        ("Slicing", "refresh", "Refreshed ·"),
        ("Stopped", "start", "From last run · captured"),
        ("Stopped", "refresh", "From last run · refreshed"),
        ("Stopped", "refresh_stopped", "Not running · refreshed"),
    ],
)
def test_config_header_names_the_capture_source(app, client, state, source, caption):
    _login(client)
    slicer_id = _make_slicer(app)
    _store_config(app, slicer_id, state=state, source=source)
    header = _config_header_text(client.get(f"/slicers/{slicer_id}/config").data.decode())
    assert header.startswith(caption + ' <time class="local-time"')


def test_config_refresh_while_running_is_labelled_refreshed(app, client):
    from app.uplynk.slicer_config import SlicerConfig

    _login(client)
    slicer_id = _make_slicer_with_scoped_key(app)
    _store_config(app, slicer_id)
    with patch(
        "app.slicers.service.UplynkDiscoveryClient.retrieve_slicer_config",
        return_value=SlicerConfig(protocol="SRT"),
    ):
        body = client.post(f"/slicers/{slicer_id}/config/refresh").data.decode()
    assert _config_header_text(body).startswith("Refreshed · ")


# --- "Unknown end point" hint (#55) ---


def _control(client, slicer_id, result, method="content_start"):
    with patch("app.slicers.service.call_slicer", return_value=result):
        return client.post(f"/slicers/{slicer_id}/control", data={"method": method})


def test_unknown_endpoint_404_shows_hint_and_raw_response(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    response = _control(
        client, slicer_id, CSLResult(404, {"error": 2, "msg": "Unknown end point"}, False)
    )
    body = response.data.decode()
    assert "The slicer didn't recognise this command" in body
    assert "slicer version or configuration issue" in body
    # Raw response still shown below the hint.
    assert "HTTP 404" in body
    assert "Unknown end point" in body
    # Audit log keeps the raw response.
    with app.app_context():
        event = db.session.query(AuditEvent).one()
        assert "Unknown end point" in event.response_snippet


def test_other_errors_have_no_hint(app, client):
    _login(client)
    slicer_id = _make_slicer(app)
    body = _control(client, slicer_id, CSLResult(500, {"msg": "boom"}, False)).data.decode()
    assert "recognise this command" not in body
    assert "HTTP 500" in body
