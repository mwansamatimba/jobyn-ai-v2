"""Authentication HTTP routes.

Thin endpoints that delegate authentication use cases to AuthService.
Request-scoped database sessions and authenticated-user resolution are provided
by backend.api.deps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.api.deps import get_auth_service, get_current_user
from backend.models.user import User
from backend.schemas.auth import TokenResponse, UserLogin
from backend.schemas.user import UserCreate, UserRead
from backend.services.auth import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


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
    """Create a new user account."""

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
    """Authenticate credentials and return a JWT access token."""

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
    """Return the profile of the authenticated user."""

    return UserRead.model_validate(current_user)