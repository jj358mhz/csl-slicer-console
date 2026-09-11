"""Tests for the crypto helper."""

from __future__ import annotations

import pytest

from app.crypto import CryptoError, decrypt, encrypt, mask


def test_roundtrip(app):
    with app.app_context():
        secret = "Vk1234567890abcdef"
        assert decrypt(encrypt(secret)) == secret


def test_ciphertext_differs_from_plaintext(app):
    with app.app_context():
        secret = "some-api-key"
        assert encrypt(secret) != secret


def test_two_encryptions_differ(app):
    """Fernet includes a random IV, so ciphertexts should not repeat."""
    with app.app_context():
        secret = "same-input"
        assert encrypt(secret) != encrypt(secret)


def test_decrypt_bad_ciphertext_raises(app):
    with app.app_context(), pytest.raises(CryptoError):
        decrypt("not-a-valid-token")


def test_mask_short_secret():
    assert mask("abcd") == "••••"


def test_mask_long_secret():
    result = mask("Vk1234567890abcdef")
    assert result.startswith("Vk12")
    assert result.endswith("cdef")
    assert "•" in result


def test_mask_empty():
    assert mask("") == ""
