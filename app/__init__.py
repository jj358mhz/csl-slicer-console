"""Flask application factory."""

from __future__ import annotations

import logging
import os

from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from app.bootstrap import bootstrap_admin
from app.config import Config
from app.models import User, db

APP_VERSION = os.getenv("APP_VERSION", "dev")

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to access that page."
login_manager.login_message_category = "info"

csrf = CSRFProtect()


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


def create_app(config: Config | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config or Config())

    _configure_logging(app)

    # Extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Blueprints
    from app.admin import bp as admin_bp
    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.slicers import bp as slicers_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(slicers_bp)

    # Expose app version to all templates
    @app.context_processor
    def inject_version() -> dict:
        return {"app_version": APP_VERSION}

    # First-run bootstrap (idempotent — skips if users already exist)
    bootstrap_admin(app)

    app.logger.info("csl-slicer-console started (env=%s)", app.config.get("ENV", "?"))
    return app


def _configure_logging(app: Flask) -> None:
    """Send Flask logs to stdout at the configured level."""
    level_name = os.getenv("LOG_LEVEL", "info").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s in %(name)s: %(message)s"))
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(level)
