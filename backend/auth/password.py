"""Password hashing helpers using Argon2 with bcrypt fallback.

This module prefers Argon2id for new hashes when the optional
`argon2-cffi` package is installed. If Argon2 is unavailable, it falls back to
bcrypt and still verifies existing bcrypt hashes.
"""

from __future__ import annotations

from typing import Final

import bcrypt

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHash, VerifyMismatchError
    from argon2.low_level import Type
except ImportError:  # pragma: no cover
    PasswordHasher = None  # type: ignore[assignment]
    InvalidHash = ValueError  # type: ignore[assignment]
    VerifyMismatchError = ValueError  # type: ignore[assignment]
    Type = None  # type: ignore[assignment]

ALGORITHM_ARGON2_PREFIX: Final = "$argon2"
ALGORITHM_BCRYPT_PREFIXES: Final[tuple[str, ...]] = ("$2a$", "$2b$", "$2y$")

if PasswordHasher is not None:
    _password_hasher = PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
else:
    _password_hasher = None  # type: ignore[assignment]


def _hash_with_bcrypt(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_with_bcrypt(password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    """Hash a plain-text password.

    Argon2id is used when available. Otherwise bcrypt is used as a secure
    fallback.
    """
    if _password_hasher is not None:
        return _password_hasher.hash(password)

    return _hash_with_bcrypt(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against an Argon2 or bcrypt hash."""
    if hashed_password.startswith(ALGORITHM_ARGON2_PREFIX):
        if _password_hasher is None:
            return False
        try:
            return _password_hasher.verify(hashed_password, plain_password)
        except (InvalidHash, VerifyMismatchError):
            return False

    if hashed_password.startswith(ALGORITHM_BCRYPT_PREFIXES):
        return _verify_with_bcrypt(plain_password, hashed_password)

    if _password_hasher is not None:
        try:
            return _password_hasher.verify(hashed_password, plain_password)
        except (InvalidHash, VerifyMismatchError):
            return False

    return False


__all__ = ["hash_password", "verify_password"]
