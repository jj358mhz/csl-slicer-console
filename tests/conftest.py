"""Shared pytest fixtures."""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Create a Flask app configured for tests with a temp SQLite DB."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "test@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "test-password-123")
    monkeypatch.setenv("WTF_CSRF_ENABLED", "false")

    from app import create_app
    from app.bootstrap import bootstrap_admin
    from app.models import db

    flask_app = create_app()
    with flask_app.app_context():
        db.create_all()
        # Now that tables exist, run bootstrap explicitly (create_app's call
        # earlier was a no-op because the schema wasn't there yet).
        bootstrap_admin(flask_app)
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(scope="module")
def ec_keypair():
    """Generate a real ES256 keypair for signing test JWTs."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    return {
        "private_b64": base64.b64encode(pem).decode(),
        "public_key": public_key,
    }
