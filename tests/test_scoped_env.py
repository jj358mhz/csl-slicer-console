"""Tests for the Uplynk .env file parser."""

from __future__ import annotations

import pytest

from app.uplynk.scoped_env import ScopedEnvParseError, parse_scoped_env


def test_parses_all_required_fields():
    content = """
KID=key-123
SUB=owner-abc
PRIVATE_B64=aGVsbG8=
SCP=video.services.ingest.cloudslicer.live:read
ROOT_URL=https://services.uplynk.com
"""
    result = parse_scoped_env(content)
    assert result.kid == "key-123"
    assert result.sub == "owner-abc"
    assert result.private_b64 == "aGVsbG8="
    assert result.scp == "video.services.ingest.cloudslicer.live:read"
    assert result.root_url == "https://services.uplynk.com"


def test_root_url_is_optional():
    content = "KID=k\nSUB=s\nPRIVATE_B64=x\nSCP=y\n"
    result = parse_scoped_env(content)
    assert result.root_url is None


def test_strips_quotes():
    content = "KID=\"key-123\"\nSUB='owner'\nPRIVATE_B64=b\nSCP=s\n"
    result = parse_scoped_env(content)
    assert result.kid == "key-123"
    assert result.sub == "owner"


def test_accepts_bytes():
    content = b"KID=k\nSUB=s\nPRIVATE_B64=b\nSCP=x\n"
    result = parse_scoped_env(content)
    assert result.kid == "k"


def test_ignores_comments_and_blank_lines():
    content = """
# This is a comment
KID=k

SUB=s
# another comment
PRIVATE_B64=b
SCP=x
"""
    result = parse_scoped_env(content)
    assert result.kid == "k"


def test_missing_required_field_raises():
    content = "KID=k\nSUB=s\nSCP=x\n"  # no PRIVATE_B64
    with pytest.raises(ScopedEnvParseError, match="PRIVATE_B64"):
        parse_scoped_env(content)


def test_malformed_line_raises():
    content = "KID=k\nnot a valid line\nSUB=s\n"
    with pytest.raises(ScopedEnvParseError, match="Line 2"):
        parse_scoped_env(content)


def test_invalid_utf8_raises():
    with pytest.raises(ScopedEnvParseError, match="UTF-8"):
        parse_scoped_env(b"\xff\xfe\x00\x00")
