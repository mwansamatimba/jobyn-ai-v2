"""Reusable FastAPI dependency injection module.

Single source of truth for request-scoped dependencies: database sessions,
repositories, services and the authenticated user. Route modules consume these
dependencies instead of building repositories or services themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

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


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login"
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped async database session.

    Only the session lifecycle is managed here; committing and rolling back
    are left to the surrounding unit of work.

    Yields:
        An open AsyncSession for the current request.
    """
    async with async_session_maker() as session:
        yield session


async def get_user_repository(
    session: AsyncSession = Depends(get_session),
) -> UserRepository:
    """Provide a request-scoped UserRepository.

    Args:
        session: The request-scoped async database session.

    Returns:
        A repository bound to the User model.
    """
    return UserRepository(
        session=session,
        model=User,
    )


async def get_auth_service(
    repository: UserRepository = Depends(get_user_repository),
) -> AuthService:
    """Provide a request-scoped AuthService.

    Args:
        repository: Injected UserRepository.

    Returns:
        AuthService instance.
    """
    return AuthService(repository)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    repository: UserRepository = Depends(get_user_repository),
) -> User:
    """Resolve authenticated user from bearer token.

    Args:
        token:
            JWT bearer token.

        repository:
            User repository dependency.

    Returns:
        Authenticated User instance.

    Raises:
        InvalidTokenError:
            If token subject is invalid or user does not exist.
    """
    payload = verify_token(token)

    try:
        subject = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError() from exc

    user = await repository.get(subject)

    if user is None:
        raise InvalidTokenError()

    return user