"""Reusable FastAPI dependency injection module.

Centralized request-scoped dependencies for database sessions,
repositories, authentication services, and authenticated users.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from backend.auth.jwt import InvalidTokenError, verify_token
from backend.core.config import get_settings
from backend.database.session import async_session_maker
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.services.admin_accounts import AdminAccountService
from backend.services.auth import AuthService

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession


__all__ = [
    "get_auth_service",
    "get_current_user",
    "get_session",
    "get_user_repository",
    "oauth2_scheme",
    "require_admin",
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


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    repository: UserRepository = Depends(get_user_repository),
) -> User:
    """Resolve the authenticated user from a JWT bearer token."""

    payload = verify_token(token)

    try:
        subject = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError() from exc

    user = await repository.get(subject)

    if user is None:
        raise InvalidTokenError()

    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Require an active administrator, preserving ADMIN_EMAILS as bootstrap."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator account is inactive.",
        )

    if current_user.role == "admin":
        return current_user

    allowed = {email.lower() for email in get_settings().ADMIN_EMAILS}
    if current_user.email.lower() in allowed:
        return await AdminAccountService().bootstrap_admin(
            session,
            user=current_user,
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator access required.",
    )
