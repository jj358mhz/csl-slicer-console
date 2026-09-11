"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # no-op in Docker; useful for local `flask run` outside container


def _require(name: str) -> str:
    """Read a required env var or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill in real values."
        )
    return value


class Config:
    """Base config — instance attributes so env vars are read fresh at
    each instantiation, which makes testing with monkeypatch reliable."""

    def __init__(self) -> None:
        self.ENV = os.getenv("FLASK_ENV", "production")
        self.SECRET_KEY = _require("SECRET_KEY")
        self.FERNET_KEY = _require("FERNET_KEY")

        self.SQLALCHEMY_DATABASE_URI = os.getenv(
            "DATABASE_URL",
            "sqlite:////app/data/csl-slicer-console.db",
        )
        self.SQLALCHEMY_TRACK_MODIFICATIONS = False

        self.BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
        self.BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")

        self.UPLYNK_API_BASE = os.getenv("UPLYNK_API_BASE", "https://services.uplynk.com")
        self.DISCOVERY_SYNC_INTERVAL = int(os.getenv("DISCOVERY_SYNC_INTERVAL", "15"))
