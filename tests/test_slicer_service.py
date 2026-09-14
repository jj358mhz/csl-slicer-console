"""Tests for the slicer control service (auth, dry-run, audit log)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import AuditEvent, Slicer, UplynkAccount, User, db
from app.slicers.service import (
    SlicerAccessDenied,
    control_slicer,
    poll_account_slicer_states,
)
from app.uplynk.csl import CSLError, CSLResult
from app.uplynk.discovery import DiscoveredSlicer, UplynkAPIError


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


def _mk_account_scene(app, *, n_slicers=3, admin=False, assign_first_n=None):
    """Build one scoped-key account with N slicers and a user.

    - `assign_first_n` (regular users only): assign the user to the first N
      slicers. Defaults to all of them. Ignored for admin.
    Returns dict with user_id, account_id, and slicer_ids (in creation order).
    """
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
        for i in range(n_slicers):
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

        user = User(
            email="controller@example.com",
            password_hash=hash_password("password-1234"),
            is_admin=admin,
        )
        db.session.add(user)
        db.session.flush()

        if not admin:
            n_assign = n_slicers if assign_first_n is None else assign_first_n
            for sid in slicer_ids[:n_assign]:
                user.slicers.append(db.session.get(Slicer, sid))
        db.session.commit()
        return {
            "user_id": user.id,
            "account_id": acct.id,
            "slicer_ids": slicer_ids,
        }


def _fresh(slicer_id, state="Slicing", connection_mode="pull"):
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
    )


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


def test_poll_account_states_makes_one_list_call(app):
    """One list_slicers() call per poll, regardless of slicer count."""
    ids = _mk_account_scene(app, n_slicers=5, admin=True)
    fresh_list = [_fresh(f"s{i + 1}") for i in range(5)]

    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        account = db.session.get(UplynkAccount, ids["account_id"])
        with patch(
            "app.slicers.service.UplynkDiscoveryClient.list_slicers",
            return_value=fresh_list,
        ) as mock_list:
            result = poll_account_slicer_states(user, account)

        assert mock_list.call_count == 1
        assert len(result) == 5
        # All five got the fresh state
        for s in result:
            assert s.last_state == "Slicing"


def test_poll_account_states_regular_user_gets_only_assigned_subset(app):
    """A user assigned to 2 of 5 slicers sees only those 2 in the result."""
    ids = _mk_account_scene(app, n_slicers=5, assign_first_n=2)
    fresh_list = [_fresh(f"s{i + 1}") for i in range(5)]

    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        account = db.session.get(UplynkAccount, ids["account_id"])
        with patch(
            "app.slicers.service.UplynkDiscoveryClient.list_slicers",
            return_value=fresh_list,
        ):
            result = poll_account_slicer_states(user, account)

        returned_ids = {s.slicer_id for s in result}
        assert returned_ids == {"s1", "s2"}
        # And the unassigned slicers weren't touched
        s3 = db.session.query(Slicer).filter_by(slicer_id="s3").one()
        assert s3.last_state == "Stopped"


def test_poll_account_states_skips_write_when_all_unchanged(app):
    """Second poll with identical values makes no DB write (see #15)."""
    ids = _mk_account_scene(app, n_slicers=3, admin=True)
    fresh_list = [_fresh(f"s{i + 1}", state="Slicing", connection_mode="pull") for i in range(3)]

    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        account = db.session.get(UplynkAccount, ids["account_id"])
        # First poll: state changes from "Stopped" to "Slicing" — writes happen.
        with patch(
            "app.slicers.service.UplynkDiscoveryClient.list_slicers",
            return_value=fresh_list,
        ):
            poll_account_slicer_states(user, account)

        # Snapshot last_seen_at after the first poll for every slicer.
        seen_after_first = {s.slicer_id: s.last_seen_at for s in db.session.query(Slicer).all()}
        for ts in seen_after_first.values():
            assert ts is not None

        # Second poll: identical fresh values → no write, last_seen_at unchanged.
        with patch(
            "app.slicers.service.UplynkDiscoveryClient.list_slicers",
            return_value=fresh_list,
        ):
            poll_account_slicer_states(user, account)

        for s in db.session.query(Slicer).all():
            assert s.last_seen_at == seen_after_first[s.slicer_id]


def test_poll_account_states_denies_user_with_no_assignments(app):
    """A regular user with zero slicers on this account gets SlicerAccessDenied."""
    ids = _mk_account_scene(app, n_slicers=3, assign_first_n=0)

    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        account = db.session.get(UplynkAccount, ids["account_id"])
        with pytest.raises(SlicerAccessDenied):
            poll_account_slicer_states(user, account)


def test_poll_account_states_missing_from_fresh_leaves_row_untouched(app):
    """A slicer Uplynk didn't return keeps its stale row (matches single-slicer path)."""
    ids = _mk_account_scene(app, n_slicers=3, admin=True)
    # Only s1 and s2 come back — s3 is absent from Uplynk's list response.
    fresh_list = [_fresh("s1"), _fresh("s2")]

    with app.app_context():
        user = db.session.get(User, ids["user_id"])
        account = db.session.get(UplynkAccount, ids["account_id"])
        with patch(
            "app.slicers.service.UplynkDiscoveryClient.list_slicers",
            return_value=fresh_list,
        ):
            result = poll_account_slicer_states(user, account)

        # s3 is still in the returned list (with stale data), so the template
        # can render its last-known badge.
        by_id = {s.slicer_id: s for s in result}
        assert by_id["s1"].last_state == "Slicing"
        assert by_id["s2"].last_state == "Slicing"
        assert by_id["s3"].last_state == "Stopped"
        assert by_id["s3"].last_seen_at is None
