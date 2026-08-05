"""FastAPI dependency injection wiring.

This module is the composition root for request-scoped dependencies. Endpoints
receive sessions, repositories, and services through ``Depends`` rather than
constructing them directly, which keeps layers decoupled and testable.

The ``get_current_token_payload`` dependency validates the ``Authorization:
Bearer <jwt>`` header and returns the decoded claims, and
``get_current_user`` resolves the token subject against the ``User`` model.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AuthenticationError
from backend.core.security import decode_token
from backend.database.session import async_session_factory
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.services.user import UserService

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped async session and ensure it is always closed."""
    async with async_session_factory() as session:
        yield session


async def get_current_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """Validate the bearer token and return its decoded claims."""
    if credentials is None:
        raise AuthenticationError("Missing or invalid Authorization header")
    return decode_token(credentials.credentials)


def get_user_repository(session: AsyncSession = Depends(get_db)) -> UserRepository:
    """Build a request-scoped user repository."""
    return UserRepository(session, User)


def get_user_service(
    repository: UserRepository = Depends(get_user_repository),
) -> UserService:
    """Build a request-scoped user service."""
    return UserService(repository)


async def get_current_user(
    payload: dict[str, Any] = Depends(get_current_token_payload),
    service: UserService = Depends(get_user_service),
) -> User:
    """Resolve the authenticated account from the bearer token subject."""
    subject = payload.get("sub")
    if subject is None:
        raise AuthenticationError("Token is missing a subject")

    try:
        user_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise AuthenticationError("Token subject is not a valid user id") from exc

    user = await service.repository.get(user_id)
    if user is None:
        raise AuthenticationError("Account no longer exists")
    return user
