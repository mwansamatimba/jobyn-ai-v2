"""Authentication HTTP routes.

Thin endpoints that delegate every use case to the :class:`AuthService` and
rely on the existing :mod:`backend.auth.jwt` helpers for token verification.
No database queries, business logic, hashing or token creation happens in this
module.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordBearer

from backend.auth.jwt import InvalidTokenError, verify_token
from backend.database.session import get_session
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.schemas.auth import TokenResponse, UserLogin
from backend.schemas.user import UserCreate, UserRead
from backend.services.auth import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_auth_service(
    session: AsyncSession = Depends(get_session),
) -> AuthService:
    """Build a request-scoped :class:`AuthService` with its repository.

    Args:
        session: The request-scoped async database session.

    Returns:
        A configured :class:`AuthService`.
    """
    repository = UserRepository(
        session=session,
        model=User,
    )
    return AuthService(repository)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the authenticated user from the bearer token.

    Missing tokens are rejected by ``oauth2_scheme``. Invalid signatures,
    malformed tokens and expired tokens surface the dedicated exceptions from
    :mod:`backend.auth.jwt`. A token whose subject does not map to an existing
    active user is treated as invalid so account existence is never disclosed.

    Args:
        token: The bearer token extracted from the ``Authorization`` header.
        session: The request-scoped async database session.

    Returns:
        The authenticated :class:`User` instance.

    Raises:
        InvalidTokenError: If the token subject is missing or does not
            reference an existing user.
    """
    payload = verify_token(token)

    try:
        subject = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError() from exc

    repository = UserRepository(
        session=session,
        model=User,
    )
    user = await repository.get(subject)
    if user is None:
        raise InvalidTokenError()

    return user


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    user_data: UserCreate,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserRead:
    """Create a new account and return its public profile.

    Args:
        user_data: Validated registration payload.
        auth_service: The injected authentication service.

    Returns:
        The created user's public representation.

    Raises:
        EmailAlreadyRegisteredError: If the email is already in use (HTTP 409).
    """
    return await auth_service.register_user(user_data)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and issue an access token",
)
async def login(
    data: UserLogin,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Verify credentials and return a signed access token.

    Args:
        data: Validated login payload.
        auth_service: The injected authentication service.

    Returns:
        A :class:`TokenResponse` containing the signed access token.

    Raises:
        InvalidCredentialsError: If the credentials are invalid (HTTP 401).
    """
    user = await auth_service.authenticate_user(
        email=data.email,
        password=data.password,
    )
    return auth_service.create_access_token_response(user)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user's profile",
)
async def read_current_user(
    current_user: User = Depends(get_current_user),
) -> UserRead:
    """Return the profile of the user identified by the bearer token.

    Args:
        current_user: The user resolved from the bearer token.

    Returns:
        The authenticated user's public representation.
    """
    return UserRead.model_validate(current_user)
