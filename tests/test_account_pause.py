"""Pausing an Uplynk account: no syncing or polling, and left out of lists."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AdminEvent, Slicer, UplynkAccount, User, db

READ_WRITE = (
    "video.services.ingest.slicer.cloudslicer.live:read,"
    "video.services.ingest.slicer.cloudslicer.live:write"
)
LIST_SLICERS = "app.uplynk.discovery.UplynkDiscoveryClient.list_slicers"
ADMIN = ("test@example.com", "test-password-123")
PLAIN = ("user@example.com", "user-password-123")


def _account(label, paused=False):
    acct = UplynkAccount(
        label=label,
        workspace_id=f"ws-{label}",
        legacy_api_key_encrypted=encrypt("k"),
        scoped_kid="kid",
        scoped_sub="sub",
        scoped_private_b64_encrypted=encrypt("key"),
        scoped_scp=READ_WRITE,
        is_paused=paused,
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _slicer(account, slicer_id):
    slicer = Slicer(
        uplynk_account_id=account.id,
        slicer_id=slicer_id,
        slicer_api_url=f"https://ingest.example.com/{slicer_id}",
        is_active=True,
        last_state="Stopped",
        region="us-east-1",
    )
    db.session.add(slicer)
    db.session.commit()
    return slicer


def _login(client, credentials=ADMIN):
    client.post("/auth/login", data={"email": credentials[0], "password": credentials[1]})


def _plain_user(*slicers):
    user = User(email=PLAIN[0], password_hash=hash_password(PLAIN[1]), is_admin=False)
    user.slicers = list(slicers)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def paused_and_live(app):
    """A paused account and a live one, with one slicer each."""
    paused = _account("paused_acct", paused=True)
    live = _account("live_acct")
    return _slicer(paused, "paused_slicer"), _slicer(live, "live_slicer")


def test_accounts_are_not_paused_by_default(app):
    assert _account("acct").is_paused is False


def test_pause_and_resume_are_recorded(app, client):
    acct = _account("acct")
    db.session.commit()
    _login(client)

    client.post(f"/admin/uplynk-accounts/{acct.id}/pause")
    assert db.session.get(UplynkAccount, acct.id).is_paused is True
    body = client.get("/admin/uplynk-accounts").get_data(as_text=True)
    assert "badge--paused" in body
    assert "Resume" in body
    assert "Resume this account to sync it" in body

    client.post(f"/admin/uplynk-accounts/{acct.id}/resume")
    db.session.expire_all()
    assert db.session.get(UplynkAccount, acct.id).is_paused is False
    assert "badge--paused" not in client.get("/admin/uplynk-accounts").get_data(as_text=True)

    actions = [e.action for e in db.session.query(AdminEvent).order_by(AdminEvent.id)]
    assert actions[-2:] == ["pause", "resume"]


@pytest.mark.parametrize("step", ["pause", "resume"])
def test_pausing_is_admin_only(app, client, step):
    acct = _account("acct")
    _plain_user()
    _login(client, PLAIN)
    assert client.post(f"/admin/uplynk-accounts/{acct.id}/{step}").status_code == 403
    assert db.session.get(UplynkAccount, acct.id).is_paused is False


def test_pausing_leaves_the_slicers_as_they_were(app, client):
    acct = _account("acct")
    slicer = _slicer(acct, "s")
    user = _plain_user(slicer)
    _login(client)
    client.post(f"/admin/uplynk-accounts/{acct.id}/pause")
    client.post(f"/admin/uplynk-accounts/{acct.id}/resume")
    db.session.expire_all()
    assert slicer.is_active is True
    assert [s.id for s in db.session.get(User, user.id).slicers] == [slicer.id]


def test_a_paused_account_is_not_synced(app, client, paused_and_live):
    paused, _ = paused_and_live
    _login(client)
    with patch(LIST_SLICERS) as list_slicers:
        response = client.post(
            f"/admin/uplynk-accounts/{paused.uplynk_account_id}/sync",
            headers={"Accept": "application/json"},
        )
    list_slicers.assert_not_called()
    assert "paused" in response.get_json()["error"]
    assert paused.is_active is True


def test_sync_all_skips_paused_accounts(app, client, paused_and_live):
    _login(client)
    with patch(LIST_SLICERS, return_value=[]) as list_slicers:
        client.post("/admin/slicer-updates/sync-all")
    assert list_slicers.call_count == 1


def test_a_paused_account_is_not_polled(app, client, paused_and_live):
    paused, _ = paused_and_live
    _login(client)
    with patch(LIST_SLICERS) as list_slicers:
        response = client.get(f"/slicers/accounts/{paused.uplynk_account_id}/slicer-states")
    list_slicers.assert_not_called()
    assert response.status_code == 403


def test_a_paused_slicer_cannot_be_reached_directly(app, client, paused_and_live):
    paused, _ = paused_and_live
    paused.thumb_url = "https://example.com/thumb.jpg"
    db.session.commit()
    _login(client)
    assert client.get(f"/slicers/{paused.id}/state").status_code == 403
    assert client.get(f"/slicers/{paused.id}/thumb").status_code == 403
    assert client.post(f"/slicers/{paused.id}/control", data={"method": "start"}).status_code == 403


@pytest.mark.parametrize(
    "url", ["/", "/admin/slicers", "/admin/slicer-updates", "/admin/slicers/new"]
)
def test_lists_leave_out_paused_accounts(app, client, paused_and_live, url):
    _login(client)
    with patch("app.uplynk.discovery.UplynkDiscoveryClient.list_slicer_snapshots", return_value={}):
        body = client.get(url).get_data(as_text=True)
    assert "paused_slicer" not in body
    assert "paused_acct" not in body
    assert "live_acct" in body


def test_dashboard_leaves_out_paused_slicers_for_regular_users(app, client, paused_and_live):
    _plain_user(*paused_and_live)
    _login(client, PLAIN)
    body = client.get("/").get_data(as_text=True)
    assert "paused_slicer" not in body
    assert "live_slicer" in body


def test_saving_a_user_keeps_their_paused_assignments(app, client, paused_and_live):
    paused, live = paused_and_live
    user = _plain_user(paused)
    _login(client)

    assign_page = f"/admin/users/{user.id}/slicers"
    assert "paused_slicer" not in client.get(assign_page).get_data(as_text=True)
    client.post(assign_page, data={"slicer_ids": [live.id]})

    db.session.expire_all()
    assert {s.id for s in db.session.get(User, user.id).slicers} == {paused.id, live.id}


def test_a_paused_slicer_can_still_be_edited_directly(app, client, paused_and_live):
    paused, _ = paused_and_live
    _login(client)
    body = client.get(f"/admin/slicers/{paused.id}/edit").get_data(as_text=True)
    assert "paused_acct" in body


def test_audit_log_still_shows_paused_slicers(app, client, paused_and_live):
    _login(client)
    assert "paused_slicer" in client.get("/admin/audit").get_data(as_text=True)
