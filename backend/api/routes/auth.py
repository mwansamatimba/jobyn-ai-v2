"""Authentication HTTP routes.

Thin endpoints that delegate authentication use cases to AuthService.
Request-scoped database sessions and authenticated-user resolution are provided
by backend.api.deps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

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
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Authenticate JSON frontend credentials or OAuth2 password-form credentials.

    The frontend sends ``{"email": ..., "password": ...}`` as JSON, while
    Swagger's OAuth2 password flow sends ``username=...&password=...`` as
    ``application/x-www-form-urlencoded``. Both representations are accepted
    at this single endpoint so the existing authentication implementation and
    JWT/token validation remain unchanged.
    """

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    if content_type == "application/x-www-form-urlencoded":
        form = await request.form()
        data = UserLogin.model_validate(
            {
                "email": form.get("username"),
                "password": form.get("password"),
            }
        )
    else:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise RequestValidationError(
                [{"type": "json_invalid", "loc": ("body",), "msg": "Invalid JSON", "input": None}]
            ) from exc
        try:
            data = UserLogin.model_validate(payload)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

    user = await auth_service.authenticate_user(
        email=str(data.email),
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
