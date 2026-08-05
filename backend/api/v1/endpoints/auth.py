"""Authentication endpoints.

Routes only orchestrate: they call into the service layer and map results to
HTTP responses. No business logic lives here.
"""

from fastapi import APIRouter, Depends, status

from backend.api.deps import get_current_user, get_user_service
from backend.models.user import User
from backend.schemas.auth import TokenResponse, UserLogin
from backend.schemas.user import UserCreate, UserRead
from backend.services.user import UserService

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: UserCreate,
    service: UserService = Depends(get_user_service),
) -> User:
    """Create a new account."""
    return await service.register(payload)


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    payload: UserLogin,
    service: UserService = Depends(get_user_service),
) -> TokenResponse:
    """Exchange valid credentials for an access token."""
    user = await service.authenticate(payload.email, payload.password)
    return service.issue_access_token(user)


@router.get("/auth/me", response_model=UserRead)
async def read_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the authenticated account."""
    return current_user
