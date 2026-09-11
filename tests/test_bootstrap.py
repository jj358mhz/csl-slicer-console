"""Tests for admin bootstrap behavior."""

from __future__ import annotations

from app.auth.passwords import verify_password
from app.models import User, db


def test_bootstrap_creates_admin_when_empty(app):
    with app.app_context():
        admin = db.session.query(User).filter_by(email="test@example.com").first()
        assert admin is not None
        assert admin.is_admin is True
        assert admin.is_active is True
        assert verify_password(admin.password_hash, "test-password-123")


def test_bootstrap_is_idempotent(app):
    """Calling create_app again shouldn't create a duplicate admin."""
    from app import create_app

    _ = create_app()  # second call
    with app.app_context():
        count = db.session.query(User).filter_by(email="test@example.com").count()
        assert count == 1
