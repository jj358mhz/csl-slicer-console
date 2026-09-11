"""One-time bootstrap: create the initial admin user if the DB is empty."""

from __future__ import annotations

from flask import Flask
from sqlalchemy import inspect

from app.auth.passwords import hash_password
from app.models import User, db


def bootstrap_admin(app: Flask) -> None:
    """Create the bootstrap admin user if no users exist.

    No-ops if the users table doesn't exist yet (e.g. before migrations
    have run, or in tests before db.create_all()).
    """
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table("users"):
            app.logger.info("users table not present yet — skipping bootstrap")
            return

        if db.session.query(User).count() > 0:
            return

        email = app.config.get("BOOTSTRAP_ADMIN_EMAIL")
        password = app.config.get("BOOTSTRAP_ADMIN_PASSWORD")

        if not email or not password:
            app.logger.warning(
                "No users exist and BOOTSTRAP_ADMIN_EMAIL/PASSWORD not set — "
                "skipping bootstrap. First user must be created manually."
            )
            return

        admin = User(
            email=email,
            password_hash=hash_password(password),
            is_admin=True,
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        app.logger.info("Bootstrap admin user created: %s", email)
