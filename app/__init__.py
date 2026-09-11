"""Flask application factory."""

from __future__ import annotations

import logging
import os

from flask import Flask

from app.bootstrap import bootstrap_admin
from app.config import Config
from app.models import db


def create_app(config: Config | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config or Config())

    _configure_logging(app)

    # Extensions
    db.init_app(app)

    # Blueprints
    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    # First-run bootstrap (idempotent — skips if users already exist)
    bootstrap_admin(app)

    app.logger.info("csl-slicer-console started (env=%s)", app.config.get("ENV", "?"))
    return app


def _configure_logging(app: Flask) -> None:
    """Send Flask logs to stdout at the configured level."""
    level_name = os.getenv("LOG_LEVEL", "info").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s in %(name)s: %(message)s")
    )
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(level)
