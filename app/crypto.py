"""Symmetric encryption helpers for API keys stored at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with the key configured via the
FERNET_KEY environment variable. Round-trips strings as strings.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class CryptoError(RuntimeError):
    """Raised when encryption or decryption fails."""


def _cipher() -> Fernet:
    key = current_app.config.get("FERNET_KEY")
    if not key:
        raise CryptoError("FERNET_KEY is not configured")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise CryptoError(f"FERNET_KEY is invalid: {e}") from e


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning a URL-safe base64 ciphertext string."""
    if plaintext is None:
        raise CryptoError("Cannot encrypt None")
    token = _cipher().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet token back to its original string."""
    if not ciphertext:
        raise CryptoError("Cannot decrypt empty ciphertext")
    try:
        plaintext = _cipher().decrypt(ciphertext.encode("utf-8"))
    except InvalidToken as e:
        raise CryptoError("Invalid or tampered ciphertext") from e
    return plaintext.decode("utf-8")


def mask(secret: str, visible: int = 4) -> str:
    """Return a masked preview of a secret for display, e.g. 'Vk••••••••3fA9'."""
    if not secret:
        return ""
    if len(secret) <= visible * 2:
        return "•" * len(secret)
    return f"{secret[:visible]}{'•' * 8}{secret[-visible:]}"
