"""Production-ready JSON Web Token (JWT) utilities.

This module provides a thin, security-focused wrapper around :mod:`jwt` (PyJWT)
for issuing and validating short-lived access tokens and long-lived refresh
tokens using the HS256 HMAC-SHA256 algorithm.

Token design
------------
Every token issued by this module contains the following standard claims:

* ``sub`` -- the subject (user identifier) encoded as a string.
* ``iat`` -- the UTC timestamp at which the token was issued.
* ``exp`` -- the UTC timestamp at which the token expires.
* ``type`` -- the token kind, either ``"access"`` or ``"refresh"``.
* ``jti`` -- a unique, random UUID4 identifier used for revocation tracking.

Security behaviour
------------------
* All tokens are signed with a configurable ``SECRET_KEY`` resolved from
  :mod:`backend.core.config.settings`.
* Signatures are always verified; forged, tampered or malformed tokens are
  rejected with :class:`InvalidSignatureError` / :class:`MalformedTokenError`.
* Expired tokens are rejected by :func:`verify_token` with
  :class:`ExpiredTokenError`.
* All raised exceptions subclass :class:`HTTPException`, so they integrate with
  FastAPI error handling out of the box (status ``401 Unauthorized``).

The settings consumed by this module may be provided through
:class:`backend.core.config.settings.Settings` attributes:

* ``SECRET_KEY``
* ``ACCESS_TOKEN_EXPIRE_MINUTES``
* ``REFRESH_TOKEN_EXPIRE_DAYS``
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import jwt
from fastapi import HTTPException, status

from backend.core.config import settings

__all__ = [
    "JWTError",
    "InvalidTokenError",
    "InvalidSignatureError",
    "MalformedTokenError",
    "ExpiredTokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "verify_token",
    "get_token_expiry",
    "is_token_expired",
]

ACCESS_TOKEN_TYPE: Literal["access"] = "access"
REFRESH_TOKEN_TYPE: Literal["refresh"] = "refresh"

JWT_ALGORITHM = "HS256"

# Defaults used when the corresponding setting is not defined on ``settings``.
_DEFAULT_SECRET_KEY = "change-me-in-production"
_DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
_DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS = 7

TokenType = Literal[ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE]


class JWTError(HTTPException):
    """Base exception for all JWT failures raised by this module.

    Subclasses :class:`fastapi.HTTPException` so that unhandled token errors
    are automatically rendered by FastAPI as ``401 Unauthorized`` responses.
    """

    def __init__(
        self,
        detail: str = "Authentication failed.",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers=headers or {"WWW-Authenticate": "Bearer"},
        )


class InvalidTokenError(JWTError):
    """Raised when a token cannot be validated for any reason."""

    def __init__(
        self,
        detail: str = "Invalid token.",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, headers=headers)


class InvalidSignatureError(InvalidTokenError):
    """Raised when a token's signature fails verification.

    This indicates the token was tampered with or signed with a different
    secret key.
    """

    def __init__(
        self,
        detail: str = "Invalid token signature.",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, headers=headers)


class MalformedTokenError(InvalidTokenError):
    """Raised when a token is not a well-formed JWT.

    This covers structurally invalid values such as a non-JWT string, an
    unparseable payload, or a payload missing required claims.
    """

    def __init__(
        self,
        detail: str = "Malformed token.",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, headers=headers)


class ExpiredTokenError(InvalidTokenError):
    """Raised when a token's ``exp`` claim has passed."""

    def __init__(
        self,
        detail: str = "Token has expired.",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail=detail, headers=headers)


def _secret_key() -> str:
    """Return the configured JWT secret key.

    Returns:
        The ``SECRET_KEY`` defined on :class:`backend.core.config.settings.Settings`
        falling back to a development-only placeholder when absent.
    """
    return str(getattr(settings, "SECRET_KEY", _DEFAULT_SECRET_KEY))


def _access_token_expiry() -> timedelta:
    """Return the configured access token lifetime.

    Returns:
        A :class:`datetime.timedelta` derived from the ``ACCESS_TOKEN_EXPIRE_MINUTES``
        setting, defaulting to 30 minutes.
    """
    minutes = int(
        getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", _DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return timedelta(minutes=minutes)


def _refresh_token_expiry() -> timedelta:
    """Return the configured refresh token lifetime.

    Returns:
        A :class:`datetime.timedelta` derived from the ``REFRESH_TOKEN_EXPIRE_DAYS``
        setting, defaulting to 7 days.
    """
    days = int(
        getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", _DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS)
    )
    return timedelta(days=days)


def _normalize_subject(subject: str | UUID) -> str:
    """Coerce a subject into its canonical string representation.

    Args:
        subject: The token subject; either a string identifier or a UUID.

    Returns:
        The subject as a string.

    Raises:
        InvalidTokenError: If ``subject`` is neither a ``str`` nor a ``UUID``.
    """
    if isinstance(subject, UUID):
        return str(subject)
    if isinstance(subject, str) and subject:
        return subject
    raise InvalidTokenError(detail="Token subject must be a non-empty string or UUID.")


def _create_token(subject: str | UUID, token_type: TokenType, additional_claims: dict[str, Any] | None = None) -> str:
    """Sign and return a JWT of the given type.

    Args:
        subject: The token subject (user identifier).
        token_type: The kind of token to issue, either ``"access"`` or ``"refresh"``.
        additional_claims: Optional custom claims merged into the payload.

    Returns:
        The encoded, signed JWT as a string.

    Raises:
        InvalidTokenError: If the subject is invalid or ``additional_claims``
            attempts to override a reserved claim.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": _normalize_subject(subject),
        "iat": now,
        "exp": now + (_access_token_expiry() if token_type == ACCESS_TOKEN_TYPE else _refresh_token_expiry()),
        "type": token_type,
        "jti": str(uuid.uuid4()),
    }

    if additional_claims:
        reserved = set(payload) & set(additional_claims)
        if reserved:
            reserved_list = ", ".join(sorted(reserved))
            raise InvalidTokenError(detail=f"Cannot override reserved claim(s): {reserved_list}.")
        payload.update(additional_claims)

    return jwt.encode(payload, _secret_key(), algorithm=JWT_ALGORITHM)


def create_access_token(subject: str | UUID, additional_claims: dict[str, Any] | None = None) -> str:
    """Create a short-lived access token.

    Args:
        subject: The token subject (user identifier).
        additional_claims: Optional custom claims merged into the payload.

    Returns:
        A signed JWT of type ``"access"``.
    """
    return _create_token(subject, ACCESS_TOKEN_TYPE, additional_claims)


def create_refresh_token(subject: str | UUID, additional_claims: dict[str, Any] | None = None) -> str:
    """Create a long-lived refresh token.

    Args:
        subject: The token subject (user identifier).
        additional_claims: Optional custom claims merged into the payload.

    Returns:
        A signed JWT of type ``"refresh"``.
    """
    return _create_token(subject, REFRESH_TOKEN_TYPE, additional_claims)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a token's signature without enforcing expiry.

    The ``exp`` claim is intentionally *not* enforced here so that callers can
    distinguish a merely stale token from a genuinely invalid one.

    Args:
        token: The JWT to decode.

    Returns:
        The decoded payload as a dictionary.

    Raises:
        InvalidSignatureError: If the signature is invalid.
        MalformedTokenError: If the token is malformed or missing required claims.
    """
    try:
        payload = jwt.decode(
            token,
            _secret_key(),
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidSignatureError as exc:
        raise InvalidSignatureError() from exc
    except jwt.PyJWTError as exc:
        raise MalformedTokenError(detail=f"Malformed token: {exc}") from exc

    _validate_required_claims(payload, allow_expired=True)
    return payload


def verify_token(token: str) -> dict[str, Any]:
    """Fully verify a token and return its claims.

    This validates the signature, structure, mandatory claims and expiry. It is
    the appropriate entry point for authentication guards and dependency
    providers.

    Args:
        token: The JWT to verify.

    Returns:
        The verified payload as a dictionary.

    Raises:
        InvalidSignatureError: If the signature is invalid.
        ExpiredTokenError: If the token has expired.
        MalformedTokenError: If the token is malformed or missing required claims.
    """
    try:
        payload = jwt.decode(
            token,
            _secret_key(),
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": True, "require": ["sub", "iat", "exp", "type", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredTokenError() from exc
    except jwt.InvalidSignatureError as exc:
        raise InvalidSignatureError() from exc
    except jwt.PyJWTError as exc:
        raise MalformedTokenError(detail=f"Malformed token: {exc}") from exc

    _validate_required_claims(payload, allow_expired=False)
    return payload


def _validate_required_claims(payload: dict[str, Any], *, allow_expired: bool) -> None:
    """Validate the presence and shape of the mandatory claims.

    Args:
        payload: The decoded token payload.
        allow_expired: When ``True``, the ``exp`` claim is only checked for
            presence, not compared against the current time.

    Raises:
        MalformedTokenError: If a required claim is missing or malformed.
        ExpiredTokenError: If the token has expired and ``allow_expired`` is ``False``.
    """
    required = ("sub", "iat", "exp", "type", "jti")
    missing = [claim for claim in required if claim not in payload]
    if missing:
        missing_list = ", ".join(missing)
        raise MalformedTokenError(detail=f"Missing required claim(s): {missing_list}.")

    token_type = payload.get("type")
    if token_type not in (ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE):
        raise MalformedTokenError(detail=f"Unknown token type: {token_type!r}.")

    if not isinstance(payload.get("sub"), str):
        raise MalformedTokenError(detail="The 'sub' claim must be a string.")

    if not isinstance(payload.get("exp"), (int, float)):
        raise MalformedTokenError(detail="The 'exp' claim must be numeric.")

    if not allow_expired and datetime.fromtimestamp(payload["exp"], tz=timezone.utc) <= datetime.now(timezone.utc):
        raise ExpiredTokenError()


def get_token_expiry(token: str) -> datetime | None:
    """Return the ``exp`` claim of a token as an aware datetime.

    Args:
        token: The JWT to inspect.

    Returns:
        The token's expiry time as a timezone-aware :class:`datetime.datetime`,
        or ``None`` when the token carries no ``exp`` claim.

    Raises:
        InvalidSignatureError: If the signature is invalid.
        MalformedTokenError: If the token is malformed.
    """
    payload = decode_token(token)
    exp = payload.get("exp")
    if exp is None:
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc)


def is_token_expired(token: str) -> bool:
    """Check whether a token has expired.

    The token's signature is verified; only expiry is probed.

    Args:
        token: The JWT to inspect.

    Returns:
        ``True`` if the token is expired or malformed, ``False`` otherwise.

    Raises:
        InvalidSignatureError: If the signature is invalid.
    """
    expiry = get_token_expiry(token)
    if expiry is None:
        return True
    return expiry <= datetime.now(timezone.utc)
