"""Tests for the slicer control service (auth, dry-run, audit log)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.slicers.service import SlicerAccessDenied, control_slicer
from app.uplynk.csl import CSLError, CSLResult


def _mk_scene(app, *, admin=False, assign_slicer=True):
    """Build an account + slicer + user in the test DB. Returns their ids."""
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
        db.session.flush()

        user = User(
            email="controller@example.com",
            password_hash=hash_password("password-1234"),
            is_admin=admin,
        )
        db.session.add(user)
        db.session.flush()
        if assign_slicer and not admin:
            user.slicers.append(slicer)
        db.session.commit()
        return {"user_id": user.id, "slicer_id": slicer.id}


def test_regular_user_can_control_assigned_slicer(app):
    ids = _mk_scene(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        with patch(
            "app.slicers.service.call_slicer",
            return_value=CSLResult(200, {"state": "Running"}, True),
        ):
            outcome = control_slicer(user, slicer, "state")

        assert outcome.ok is True
        assert outcome.error is None
        assert db.session.query(AuditEvent).count() == 1


def test_regular_user_cannot_control_unassigned_slicer(app):
    ids = _mk_scene(app, assign_slicer=False)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        with pytest.raises(SlicerAccessDenied):
            control_slicer(user, slicer, "state")
        # No audit event on denied access
        assert db.session.query(AuditEvent).count() == 0


def test_admin_can_control_any_active_slicer(app):
    ids = _mk_scene(app, admin=True, assign_slicer=False)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        with patch(
            "app.slicers.service.call_slicer",
            return_value=CSLResult(200, {"state": "Running"}, True),
        ):
            outcome = control_slicer(user, slicer, "status")
        assert outcome.ok is True


def test_cannot_control_inactive_slicer(app):
    ids = _mk_scene(app, admin=True)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        slicer.is_active = False
        db.session.commit()
        with pytest.raises(SlicerAccessDenied):
            control_slicer(user, slicer, "state")


def test_unknown_method_is_recorded_as_error(app):
    ids = _mk_scene(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        outcome = control_slicer(user, slicer, "wat")
        assert outcome.ok is False
        assert "Unknown method" in outcome.error
        event = db.session.query(AuditEvent).one()
        assert event.method == "wat"
        assert event.status_code is None


def test_csl_error_is_recorded(app):
    ids = _mk_scene(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        with patch(
            "app.slicers.service.call_slicer",
            side_effect=CSLError("network down"),
        ):
            outcome = control_slicer(user, slicer, "state")
        assert outcome.ok is False
        assert "network down" in outcome.error
        event = db.session.query(AuditEvent).one()
        assert "network down" in event.response_snippet


def test_dry_run_does_not_call_api(app):
    ids = _mk_scene(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        with patch("app.slicers.service.call_slicer") as mock_call:
            outcome = control_slicer(user, slicer, "blackout", dry_run=True)
            mock_call.assert_not_called()
        assert outcome.ok is True
        event = db.session.query(AuditEvent).one()
        assert event.dry_run is True


def test_audit_event_records_status_code(app):
    ids = _mk_scene(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])
        with patch(
            "app.slicers.service.call_slicer",
            return_value=CSLResult(200, {"state": "Ready"}, True),
        ):
            control_slicer(user, slicer, "state")
        event = db.session.query(AuditEvent).one()
        assert event.status_code == 200
        assert event.method == "state"


# ---------------------------------------------------------------------------
# set_target_state — v4 PATCH path (Start/Stop)
# ---------------------------------------------------------------------------


def _mk_scene_with_scoped_key(app, *, admin=False, assign_slicer=True):
    """Build a scene where the account has a scoped API key.

    set_target_state requires this — accounts without a scoped key
    fail cleanly to the audit log rather than attempting v4 calls.
    """
    with app.app_context():
        acct = UplynkAccount(
            label="Acct",
            workspace_id="ws",
            legacy_api_key_encrypted=encrypt("legacy-key"),
            scoped_kid="test-kid",
            scoped_sub="test-sub",
            scoped_private_b64_encrypted=encrypt("test-b64-private-key"),
            scoped_scp="video.services.slicer.cloudslicer.live:write",
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
        db.session.flush()

        user = User(
            email="controller-v4@example.com",
            password_hash=hash_password("password-1234"),
            is_admin=admin,
        )
        db.session.add(user)
        db.session.flush()
        if assign_slicer and not admin:
            user.slicers.append(slicer)
        db.session.commit()
        return {"user_id": user.id, "slicer_id": slicer.id}


def test_target_state_regular_user_can_start_assigned_slicer(app):
    from app.slicers.service import set_target_state

    ids = _mk_scene_with_scoped_key(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        with patch("app.slicers.service.set_slicer_target_state") as mock_client:
            from app.uplynk.target_state import TargetStateResult

            mock_client.return_value = TargetStateResult(
                status_code=200,
                body={"id": "s1", "target_state": "Ready"},
                ok=True,
            )
            outcome = set_target_state(user, slicer, "start")

        assert outcome.ok is True
        assert outcome.result.status_code == 200
        mock_client.assert_called_once()
        assert mock_client.call_args.kwargs["target_state"] == "Ready"


def test_target_state_stop_sends_stopped_to_api(app):
    from app.slicers.service import set_target_state

    ids = _mk_scene_with_scoped_key(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        with patch("app.slicers.service.set_slicer_target_state") as mock_client:
            from app.uplynk.target_state import TargetStateResult

            mock_client.return_value = TargetStateResult(
                status_code=200,
                body={"id": "s1", "target_state": "Stopped"},
                ok=True,
            )
            set_target_state(user, slicer, "stop")

        assert mock_client.call_args.kwargs["target_state"] == "Stopped"


def test_target_state_unassigned_user_raises(app):
    from app.slicers.service import set_target_state

    ids = _mk_scene_with_scoped_key(app, assign_slicer=False)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        with pytest.raises(SlicerAccessDenied):
            set_target_state(user, slicer, "start")


def test_target_state_unknown_method_recorded_as_error(app):
    from app.slicers.service import set_target_state

    ids = _mk_scene_with_scoped_key(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        outcome = set_target_state(user, slicer, "bogus")
        assert outcome.ok is False
        assert "Unknown target-state method" in outcome.error


def test_target_state_no_scoped_key_recorded_as_error(app):
    """Account without a scoped key can't hit v4 — fail cleanly to audit."""
    from app.slicers.service import set_target_state

    # Build a scene the normal way — no scoped key on the account
    ids = _mk_scene(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        outcome = set_target_state(user, slicer, "start")
        assert outcome.ok is False
        assert "no scoped API key" in outcome.error

        event = db.session.query(AuditEvent).filter_by(method="start").one()
        assert event.status_code is None
        assert "no scoped API key" in event.response_snippet


def test_target_state_dry_run_does_not_call_api(app):
    from app.slicers.service import set_target_state

    ids = _mk_scene_with_scoped_key(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        with patch("app.slicers.service.set_slicer_target_state") as mock_client:
            outcome = set_target_state(user, slicer, "stop", dry_run=True)

        mock_client.assert_not_called()
        assert outcome.ok is True

        event = db.session.query(AuditEvent).filter_by(method="stop").one()
        assert event.dry_run is True


def test_target_state_uplynk_error_recorded(app):
    """UplynkAPIError from the client lands in the audit log, doesn't raise."""
    from app.slicers.service import set_target_state
    from app.uplynk.discovery import UplynkAPIError

    ids = _mk_scene_with_scoped_key(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        with patch("app.slicers.service.set_slicer_target_state") as mock_client:
            mock_client.side_effect = UplynkAPIError("connection refused")
            outcome = set_target_state(user, slicer, "start")

        assert outcome.ok is False
        assert "connection refused" in outcome.error


def test_target_state_403_from_api_is_not_ok(app):
    """HTTP 403 (missing :write scope) captured, not raised."""
    from app.slicers.service import set_target_state
    from app.uplynk.target_state import TargetStateResult

    ids = _mk_scene_with_scoped_key(app)
    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        slicer = db.session.get(Slicer, ids["slicer_id"])

        with patch("app.slicers.service.set_slicer_target_state") as mock_client:
            mock_client.return_value = TargetStateResult(
                status_code=403,
                body={"code": "forbidden"},
                ok=False,
            )
            outcome = set_target_state(user, slicer, "start")

        assert outcome.ok is False
        event = db.session.query(AuditEvent).filter_by(method="start").one()
        assert event.status_code == 403
