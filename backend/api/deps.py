"""Reusable FastAPI dependency injection module.

Centralized request-scoped dependencies for database sessions,
repositories, authentication services, and authenticated users.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncGenerator

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.firebase import FirebaseTokenError, verify_firebase_id_token
from backend.auth.jwt import InvalidTokenError, verify_token
from backend.database.session import async_session_maker
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.services.auth import AuthService


__all__ = [
    "oauth2_scheme",
    "get_session",
    "get_user_repository",
    "get_auth_service",
    "get_current_user",
]


# IMPORTANT:
# This must match the actual API route exposed by FastAPI.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login"
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped async database session."""

    async with async_session_maker() as session:
        yield session


async def get_user_repository(
    session: AsyncSession = Depends(get_session),
) -> UserRepository:
    """Provide a request-scoped UserRepository."""

    return UserRepository(
        session=session,
        model=User,
    )


async def get_auth_service(
    repository: UserRepository = Depends(get_user_repository),
) -> AuthService:
    """Provide a request-scoped AuthService."""

    return AuthService(repository)


def _credential_algorithm(token: str) -> str:
    """Read the JWT algorithm only to select the authoritative verifier.

    This header is never treated as proof of identity. Firebase ID tokens are
    RS256-signed, while Jobyn's existing tokens are HS256-signed. Each selected
    verifier still performs complete cryptographic and claim validation.
    """

    try:
        return str(jwt.get_unverified_header(token).get("alg", ""))
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc


async def _get_firebase_user(
    token: str,
    repository: UserRepository,
) -> User:
    """Verify Firebase credentials and resolve/provision the Jobyn user."""

    try:
        claims = verify_firebase_id_token(token)
    except FirebaseTokenError as exc:
        # An RS256 bearer credential is treated as Firebase-shaped and is never
        # reinterpreted as a Jobyn JWT after Firebase verification fails.
        raise InvalidTokenError() from exc

    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        raise InvalidTokenError("Firebase account does not contain an email address.")

    normalized_email = email.strip().lower()
    user = await repository.get_by_email(normalized_email)

    if user is None:
        random_password = secrets.token_urlsafe(48)
        unusable_password_hash = bcrypt.hashpw(
            random_password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")
        full_name = claims.get("name")
        user = await repository.create(
            email=normalized_email,
            hashed_password=unusable_password_hash,
            full_name=full_name if isinstance(full_name, str) else None,
            is_verified=bool(claims.get("email_verified", False)),
        )
        try:
            await repository.session.commit()
        except IntegrityError as exc:
            await repository.session.rollback()
            # A concurrent Firebase request may have provisioned the same email.
            user = await repository.get_by_email(normalized_email)
            if user is None:
                raise InvalidTokenError() from exc

    if not user.is_active:
        raise InvalidTokenError("This account is disabled.")

    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    repository: UserRepository = Depends(get_user_repository),
) -> User:
    """Resolve an authenticated user from Firebase or the existing Jobyn JWT.

    Firebase-shaped RS256 credentials are verified exclusively by the Firebase
    Admin SDK. Existing HS256 Jobyn credentials continue through the original
    ``verify_token`` → UUID subject → repository primary-key lookup path.
    """

    algorithm = _credential_algorithm(token)

    if algorithm == "RS256":
        return await _get_firebase_user(token, repository)

    if algorithm == "HS256":
        payload = verify_token(token)
        try:
            subject = uuid.UUID(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidTokenError() from exc

        user = await repository.get(subject)
        if user is None:
            raise InvalidTokenError()
        return user

    # Do not try a different verifier for an unsupported JWT algorithm.
    raise InvalidTokenError()
