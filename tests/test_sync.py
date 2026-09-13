"""Tests for the slicer sync service."""

from __future__ import annotations

from unittest.mock import patch

from app.crypto import encrypt
from app.models import Slicer, UplynkAccount, db
from app.uplynk.discovery import DiscoveredSlicer, UplynkAPIError
from app.uplynk.sync import sync_account


def _mk_account(app, *, with_scoped_key: bool = True):
    """Create an UplynkAccount for tests. Scoped-key fields are dummy values —
    the discovery client itself is mocked, so we don't need a real EC key."""
    with app.app_context():
        acct = UplynkAccount(
            label="Test Acct",
            workspace_id="ws-test",
            legacy_api_key_encrypted=encrypt("legacy-test"),
        )
        if with_scoped_key:
            acct.scoped_kid = "test-kid"
            acct.scoped_sub = "test-sub"
            acct.scoped_private_b64_encrypted = encrypt("dummy-private-b64")
            acct.scoped_scp = "video.services.ingest.cloudslicer.live:read"
        db.session.add(acct)
        db.session.commit()
        return acct.id


def _mk_discovered(slicer_id="s1", state="Stopped", url=None, connection_mode="pull"):
    return DiscoveredSlicer(
        slicer_id=slicer_id,
        slicer_api_url=url or f"https://ingest.example.com/{slicer_id}",
        region="us-east-1",
        protocol="SRT",
        plugin_id="test-plugin",
        plugin_version="1.0",
        state=state,
        description=None,
        connection_mode=connection_mode,
    )


def test_sync_creates_new_slicers(app):
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1"), _mk_discovered("s2")],
        ):
            result = sync_account(acct)

        assert result.error is None
        assert result.created == 2
        assert result.updated == 0
        assert db.session.query(Slicer).count() == 2


def test_sync_updates_existing_slicers(app):
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)

        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1", state="Stopped")],
        ):
            sync_account(acct)

        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1", state="Running")],
        ):
            result = sync_account(acct)

        assert result.created == 0
        assert result.updated == 1
        slicer = db.session.query(Slicer).filter_by(slicer_id="s1").one()
        assert slicer.last_state == "Running"


def test_sync_deactivates_missing_slicers(app):
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)

        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1"), _mk_discovered("s2")],
        ):
            sync_account(acct)

        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1")],
        ):
            result = sync_account(acct)

        assert result.deactivated == 1
        s2 = db.session.query(Slicer).filter_by(slicer_id="s2").one()
        assert s2.is_active is False
        s1 = db.session.query(Slicer).filter_by(slicer_id="s1").one()
        assert s1.is_active is True


def test_sync_without_scoped_key_returns_error(app):
    acct_id = _mk_account(app, with_scoped_key=False)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        result = sync_account(acct)
        assert result.error is not None
        assert "scoped" in result.error.lower()


def test_sync_api_error_returns_error(app):
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            side_effect=UplynkAPIError("boom"),
        ):
            result = sync_account(acct)
        assert result.error == "boom"
        assert result.created == 0


def test_sync_updates_last_synced_at(app):
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        assert acct.last_synced_at is None
        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[],
        ):
            sync_account(acct)
        db.session.refresh(acct)
        assert acct.last_synced_at is not None


def test_sync_captures_connection_mode(app):
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)
        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[
                _mk_discovered("srt_push", connection_mode="push"),
                _mk_discovered("srt_pull", connection_mode="pull"),
                _mk_discovered("rtmp_none", connection_mode=None),
            ],
        ):
            sync_account(acct)

        rows = {s.slicer_id: s for s in db.session.query(Slicer).all()}
        assert rows["srt_push"].connection_mode == "push"
        assert rows["srt_pull"].connection_mode == "pull"
        assert rows["rtmp_none"].connection_mode is None


def test_sync_updates_connection_mode_on_change(app):
    """If Uplynk reports a different mode, sync captures the change."""
    acct_id = _mk_account(app)
    with app.app_context():
        acct = db.session.get(UplynkAccount, acct_id)

        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1", connection_mode="pull")],
        ):
            sync_account(acct)

        with patch(
            "app.uplynk.sync.UplynkDiscoveryClient.list_slicers",
            return_value=[_mk_discovered("s1", connection_mode="push")],
        ):
            sync_account(acct)

        slicer = db.session.query(Slicer).filter_by(slicer_id="s1").one()
        assert slicer.connection_mode == "push"
