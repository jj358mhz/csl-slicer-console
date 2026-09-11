"""Slicer control service — wraps CSL calls with authorization + audit logging."""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from app.crypto import decrypt
from app.models import AuditEvent, Slicer, User, db
from app.uplynk.csl import SLICER_METHODS, CSLError, CSLResult, call_slicer


class SlicerAccessDenied(RuntimeError):
    """Raised when a user tries to control a slicer they're not assigned."""


@dataclass
class ControlOutcome:
    """Result of a control attempt, including any audit event id."""

    result: CSLResult | None
    error: str | None = None
    audit_event_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok


def _user_can_control(user: User, slicer: Slicer) -> bool:
    """Admins can control any active slicer; regular users only assigned ones."""
    if not slicer.is_active:
        return False
    if user.is_admin:
        return True
    return any(s.id == slicer.id for s in user.slicers)


def control_slicer(
    user: User,
    slicer: Slicer,
    method_name: str,
    *,
    dry_run: bool = False,
) -> ControlOutcome:
    """Attempt a control action against one slicer, logging the outcome.

    Raises SlicerAccessDenied if the user isn't allowed to control this slicer.
    All other failure modes (bad method, decrypt error, HTTP error, network
    error) are captured into ControlOutcome.error and written to audit_events.
    """
    if not _user_can_control(user, slicer):
        raise SlicerAccessDenied(f"User {user.email} is not assigned to slicer {slicer.slicer_id}")

    method_path = SLICER_METHODS.get(method_name)
    if method_path is None:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=f"Unknown method '{method_name}'",
        )

    account = slicer.uplynk_account
    try:
        api_key = decrypt(account.legacy_api_key_encrypted)
    except Exception as e:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=f"Failed to decrypt API key: {e}",
        )

    if dry_run:
        current_app.logger.info("DRY RUN — would POST %s to %s", method_path, slicer.slicer_api_url)
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            result=CSLResult(status_code=0, body="[dry-run]", ok=True),
        )

    try:
        result = call_slicer(slicer.slicer_api_url, method_path, api_key)
    except CSLError as e:
        return _record_and_return(
            user,
            slicer,
            method_name,
            dry_run,
            error=str(e),
        )

    return _record_and_return(
        user,
        slicer,
        method_name,
        dry_run,
        result=result,
    )


def _record_and_return(
    user: User,
    slicer: Slicer,
    method_name: str,
    dry_run: bool,
    *,
    result: CSLResult | None = None,
    error: str | None = None,
) -> ControlOutcome:
    """Persist an AuditEvent and return a ControlOutcome."""
    if result is not None:
        status_code = result.status_code
        snippet = result.summary
    else:
        status_code = None
        snippet = error or "unknown error"

    event = AuditEvent(
        user_id=user.id,
        slicer_id=slicer.id,
        method=method_name,
        status_code=status_code,
        response_snippet=snippet[:500] if snippet else None,
        dry_run=dry_run,
    )
    db.session.add(event)
    db.session.commit()

    return ControlOutcome(result=result, error=error, audit_event_id=event.id)
