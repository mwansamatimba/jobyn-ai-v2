"""Business logic for user registration and authentication.

All rules live here: email normalization and uniqueness, password hashing and
verification, and the decision of what the account-holder may do. Routes only
translate these operations into HTTP calls.
"""

from sqlalchemy.exc import IntegrityError

from backend.core.config import get_settings
from backend.core.errors import AuthenticationError, AuthorizationError, ConflictError
from backend.core.security import create_access_token, get_password_hash, verify_password
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.schemas.auth import TokenResponse
from backend.schemas.user import UserCreate
from backend.services.base import BaseService


class UserService(BaseService[UserRepository]):
    """Owns the account lifecycle: registration and authentication."""

    async def register(self, data: UserCreate) -> User:
        """Create a new account, raising :class:`ConflictError` on duplicates."""
        email = data.email.strip().lower()
        if await self.repository.get_by_email(email) is not None:
            raise ConflictError("An account with this email already exists")

        try:
            user = await self.repository.create(
                email=email,
                hashed_password=get_password_hash(data.password),
                full_name=data.full_name,
            )
        except IntegrityError as exc:
            await self.rollback()
            raise ConflictError("An account with this email already exists") from exc

        await self.commit()
        return user

    async def authenticate(self, email: str, password: str) -> User:
        """Verify credentials and return the account, or raise on failure."""
        user = await self.repository.get_by_email(email.strip().lower())
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid email or password")
        if not user.is_active:
            raise AuthorizationError("This account is disabled")
        return user

    def issue_access_token(self, user: User) -> TokenResponse:
        """Create a signed access token for the given account."""
        settings = get_settings()
        access_token = create_access_token(user.subject)
        return TokenResponse(
            access_token=access_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
