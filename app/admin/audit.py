"""Helper for recording admin events."""

from __future__ import annotations

from flask_login import current_user

from app.models import AdminEvent, db


def log_admin_event(
    category: str,
    action: str,
    summary: str,
    target: str | None = None,
) -> None:
    """Persist an admin activity event.

    Commits to the DB so the event is durable even if the caller does a rollback.
    Silently no-ops if there's no current user (which shouldn't happen for
    admin-only routes, but defensive).
    """
    if not current_user.is_authenticated:
        return

    event = AdminEvent(
        actor_id=current_user.id,
        category=category,
        action=action,
        summary=summary,
        target=target,
    )
    db.session.add(event)
    db.session.commit()
