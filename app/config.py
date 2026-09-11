"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # no-op in Docker; useful for local `flask run` outside container


class Config:
    """Base config — all settings sourced from environment variables."""

    ENV = os.getenv("FLASK_ENV", "production")
    SECRET_KEY = os.environ.get("SECRET_KEY") or _missing("SECRET_KEY")
    FERNET_KEY = os.environ.get("FERNET_KEY") or _missing("FERNET_KEY")

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "sqlite:////app/data/csl-slicer-console.db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
    BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")

    UPLYNK_API_BASE = os.getenv("UPLYNK_API_BASE", "https://services.uplynk.com")
    DISCOVERY_SYNC_INTERVAL = int(os.getenv("DISCOVERY_SYNC_INTERVAL", "15"))


def _missing(name: str) -> str:
    """Raise a clear error if a required env var isn't set."""
    raise RuntimeError(
        f"{name} is not set. Copy .env.example to .env and fill in real values."
    )
