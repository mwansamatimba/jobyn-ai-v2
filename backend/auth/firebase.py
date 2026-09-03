"""Firebase Admin SDK helpers for the experimental Firebase Auth integration."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import firebase_admin
from firebase_admin import auth, credentials


class FirebaseConfigurationError(RuntimeError):
    """Raised when Firebase Admin credentials are not configured."""


class FirebaseTokenError(ValueError):
    """Raised when a Firebase ID token cannot be verified."""


def _private_key() -> str:
    value = os.getenv("FIREBASE_PRIVATE_KEY")
    if not value:
        raise FirebaseConfigurationError("FIREBASE_PRIVATE_KEY is not configured")
    return value.replace("\\n", "\n")


@lru_cache(maxsize=1)
def _get_app() -> firebase_admin.App:
    """Initialize the Admin SDK once from server-side environment variables."""

    project_id = os.getenv("FIREBASE_PROJECT_ID")
    client_email = os.getenv("FIREBASE_CLIENT_EMAIL")
    if not project_id or not client_email:
        raise FirebaseConfigurationError(
            "FIREBASE_PROJECT_ID and FIREBASE_CLIENT_EMAIL are required"
        )

    service_account = {
        "type": "service_account",
        "project_id": project_id,
        "client_email": client_email,
        "private_key": _private_key(),
    }

    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app(credentials.Certificate(service_account))


def verify_firebase_id_token(token: str) -> dict[str, Any]:
    """Authoritatively verify a Firebase ID token and return its claims.

    The Admin SDK validates the signature, issuer, audience, expiry and Firebase
    token semantics. The caller must use the returned claims rather than any
    browser-supplied UID or email.
    """

    try:
        return auth.verify_id_token(token, app=_get_app(), check_revoked=True)
    except Exception as exc:
        raise FirebaseTokenError("Invalid Firebase ID token") from exc
