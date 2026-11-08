"""Single sign-on from the Authentik forward-auth gate in front of the console.

Caddy asks Authentik about every request to the console. When it lets one
through, it passes the signed-in user's email in `X-Authentik-Email` and adds
`X-Authentik-Gate` holding a secret shared with this app
(`AUTHENTIK_GATE_SECRET`). Only requests carrying that secret are believed:
anything else on the Docker network can reach the app directly and set
headers. The email must belong to an existing, active console account;
nothing is created.
"""

from __future__ import annotations

import hmac

from flask import current_app, request
from flask_login import current_user, login_user, logout_user

from app.models import User, db

GATE_HEADER = "X-Authentik-Gate"
EMAIL_HEADER = "X-Authentik-Email"
# Ends the Authentik session too; otherwise signing out of the console would
# sign straight back in on the next request.
SIGN_OUT_PATH = "/outpost.goauthentik.io/sign_out"


def gate_email() -> str | None:
    """The Authentik user's email, if this request came through the gate."""
    secret = current_app.config["AUTHENTIK_GATE_SECRET"]
    if not secret:
        return None
    presented = request.headers.get(GATE_HEADER, "")
    if not hmac.compare_digest(presented.encode(), secret.encode()):
        return None
    return request.headers.get(EMAIL_HEADER, "").strip().lower() or None


def sign_in_from_gate() -> None:
    """Sign in the console account matching the Authentik user, if there is one.

    An Authentik user with no console account leaves any existing session
    alone, so the password login still works for them.
    """
    if request.endpoint == "static":
        return
    email = gate_email()
    if email is None:
        return
    if current_user.is_authenticated and current_user.email == email:
        return
    user = db.session.query(User).filter_by(email=email).first()
    if user is None or not user.is_active:
        return
    if current_user.is_authenticated:
        # Someone else signed in to Authentik in this browser.
        logout_user()
    login_user(user)
    current_app.logger.info("Signed in %s through Authentik", user.email)
