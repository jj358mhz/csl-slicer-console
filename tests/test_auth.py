"""Tests for the auth blueprint."""

from __future__ import annotations


def test_index_redirects_to_login_when_anonymous(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_login_page_renders(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert b"Sign in" in response.data


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_login_with_valid_credentials(client):
    response = client.post(
        "/auth/login",
        data={
            "email": "test@example.com",
            "password": "test-password-123",
            "submit": "Sign in",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_login_with_bad_password(client):
    response = client.post(
        "/auth/login",
        data={
            "email": "test@example.com",
            "password": "wrong-password",
            "submit": "Sign in",
        },
        follow_redirects=True,
    )
    assert b"Invalid email or password" in response.data
