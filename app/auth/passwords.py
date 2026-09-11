"""Password hashing using Argon2."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2 hash of the password."""
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    """Return True if the password matches the hash."""
    try:
        return _hasher.verify(hash_, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
