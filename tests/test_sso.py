"""Single sign-on through the Authentik forward-auth gate."""

from __future__ import annotations

import pytest

from app.auth.passwords import hash_password
from app.models import User, db

SECRET = "gate-secret-for-tests"
ADMIN_EMAIL = "test@example.com"


@pytest.fixture
def gated(app):
    app.config["AUTHENTIK_GATE_SECRET"] = SECRET
    return app


def _gate(email, secret=SECRET):
    return {"X-Authentik-Gate": secret, "X-Authentik-Email": email}


def _user(email, active=True):
    user = User(email=email, password_hash=hash_password("user-password-123"), is_active=active)
    db.session.add(user)
    db.session.commit()
    return user


def _signed_in_as(client):
    """The email the nav shows, or None when the dashboard redirects to login."""
    response = client.get("/")
    if response.status_code != 200:
        return None
    body = response.get_data(as_text=True)
    for email in (ADMIN_EMAIL, "user@example.com", "other@example.com"):
        if email in body:
            return email
    return "?"


def test_gate_signs_in_the_matching_account(gated, client):
    assert client.get("/", headers=_gate(ADMIN_EMAIL)).status_code == 200
    # The session sticks, so later requests don't need the headers.
    assert _signed_in_as(client) == ADMIN_EMAIL


def test_email_match_ignores_case_and_spaces(gated, client):
    assert client.get("/", headers=_gate("  Test@Example.COM ")).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        _gate(ADMIN_EMAIL, secret="wrong"),
        _gate(ADMIN_EMAIL, secret=""),
        {"X-Authentik-Email": ADMIN_EMAIL},
    ],
    ids=["wrong secret", "empty secret", "no gate header"],
)
def test_headers_without_the_secret_are_ignored(gated, client, headers):
    assert client.get("/", headers=headers).status_code == 302


def test_nothing_is_trusted_when_no_secret_is_configured(app, client):
    app.config["AUTHENTIK_GATE_SECRET"] = ""
    assert client.get("/", headers=_gate(ADMIN_EMAIL, secret="")).status_code == 302


def test_inactive_accounts_are_not_signed_in(gated, client):
    _user("user@example.com", active=False)
    assert client.get("/", headers=_gate("user@example.com")).status_code == 302


def test_unknown_email_gets_the_login_page_with_a_note(gated, client):
    body = client.get("/auth/login", headers=_gate("nobody@example.com")).get_data(as_text=True)
    assert "signed in to Authentik as nobody@example.com" in body
    assert "no console account" in body


def test_unknown_email_leaves_a_password_session_alone(gated, client):
    client.post("/auth/login", data={"email": ADMIN_EMAIL, "password": "test-password-123"})
    assert client.get("/", headers=_gate("nobody@example.com")).status_code == 200
    assert _signed_in_as(client) == ADMIN_EMAIL


def test_a_different_authentik_user_switches_the_session(gated, client):
    _user("user@example.com")
    client.get("/", headers=_gate(ADMIN_EMAIL))
    client.get("/", headers=_gate("user@example.com"))
    assert _signed_in_as(client) == "user@example.com"


def test_sign_out_through_the_gate_also_ends_the_authentik_session(gated, client):
    client.get("/", headers=_gate(ADMIN_EMAIL))
    response = client.post("/auth/logout", headers=_gate(ADMIN_EMAIL))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/outpost.goauthentik.io/sign_out")


def test_sign_out_without_the_gate_goes_to_the_login_page(app, client):
    client.post("/auth/login", data={"email": ADMIN_EMAIL, "password": "test-password-123"})
    response = client.post("/auth/logout")
    assert response.headers["Location"].endswith("/auth/login")


def test_static_files_do_not_sign_in(gated, client):
    client.get("/static/css/app.css", headers=_gate(ADMIN_EMAIL))
    assert _signed_in_as(client) is None
