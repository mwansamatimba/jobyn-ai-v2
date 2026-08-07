"""Authentication service layer.

Implements the core authentication use cases of the Jobyn platform: account
registration, credential-based authentication and access-token issuance.

The service depends only on the :class:`UserRepository` for persistence and on
the framework-agnostic helpers in :mod:`backend.auth.password` and
:mod:`backend.auth.jwt` for password hashing and token generation.

Transaction ownership follows the :class:`~backend.services.base.BaseService`
pattern: repositories flush, and the service commits or rolls back at the end
of each use case so routes stay free of session lifecycle concerns.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from backend.auth.jwt import create_access_token
from backend.auth.password import hash_password, verify_password
from backend.core.config.settings import settings
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.schemas.auth import TokenResponse
from backend.schemas.user import UserCreate, UserRead
from backend.services.base import BaseService

# Precomputed hash of an arbitrary password. Verified against when no account
# matches a login attempt so that failed lookups cost roughly the same CPU time
# as a genuine hash comparison, preventing user enumeration via timing.
_DUMMY_PASSWORD_HASH: str = hash_password("timing-equalization-sentinel")

_DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30


class AuthServiceError(HTTPException):
    """Base exception for failures raised by the authentication service."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)


class EmailAlreadyRegisteredError(AuthServiceError):
    """Raised when registration targets an email address already in use."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )


class InvalidCredentialsError(AuthServiceError):
    """Raised when an authentication attempt fails.

    Deliberately indistinguishable between an unknown email, a wrong password
    and an inactive account so callers cannot enumerate registered addresses.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )


def _normalize_email(email: str) -> str:
    """Normalize an email address for storage and lookup.

    Args:
        email: The raw address supplied by the client.

    Returns:
        The address lower-cased and stripped of surrounding whitespace.
    """
    return email.strip().lower()


class AuthService(BaseService[UserRepository]):
    """Application service implementing the authentication use cases.

    Args:
        user_repository: Repository providing access to the ``User`` table.
    """

    def __init__(self, user_repository: UserRepository) -> None:
        super().__init__(user_repository)
        self.user_repository = user_repository

    async def register_user(self, data: UserCreate) -> UserRead:
        """Create a new user account and commit the transaction.

        Behavior:
            1. Rejects the request when an account with the same (normalized)
               email already exists.
            2. Hashes the plain-text password via :func:`backend.auth.password.hash_password`.
            3. Persists the user through :class:`UserRepository`, commits, and
               returns its public representation.

        Args:
            data: Validated registration payload.

        Returns:
            The created user as a client-safe :class:`UserRead`.

        Raises:
            EmailAlreadyRegisteredError: If the email is already taken, either
                detected up front or surfaced from the database unique
                constraint (race-condition safe).
        """
        email = _normalize_email(str(data.email))

        existing = await self.user_repository.get_by_email(email)
        if existing is not None:
            raise EmailAlreadyRegisteredError(email)

        hashed_password = hash_password(data.password)
        try:
            user = await self.user_repository.create(
                email=email,
                hashed_password=hashed_password,
                full_name=data.full_name,
                is_active=True,
                is_verified=False,
            )
            await self.commit()
        except IntegrityError as exc:
            await self.rollback()
            raise EmailAlreadyRegisteredError(email) from exc

        # Materialize server-side defaults (e.g. created_at) so the returned
        # instance is fully populated before it is mapped to a schema.
        await self.user_repository.session.refresh(user)

        return UserRead.model_validate(user)

    async def authenticate_user(self, email: str, password: str) -> User:
        """Authenticate a user by email and password.

        Behavior:
            1. Looks up the user by normalized email.
            2. Verifies the supplied password against the stored hash.
            3. Rejects inactive accounts.
            4. Returns the authenticated :class:`User` on success.

        All failure modes raise the same :class:`InvalidCredentialsError` with
        the same message, and the CPU time spent is equalized against a dummy
        hash when the account does not exist, mitigating user enumeration.

        Args:
            email: The address submitted at login.
            password: The plain-text password submitted at login.

        Returns:
            The authenticated user ORM instance.

        Raises:
            InvalidCredentialsError: If the email is unknown, the password is
                wrong, or the account is inactive.
        """
        email = _normalize_email(email)

        user = await self.user_repository.get_by_email(email)
        if user is None:
            _ = verify_password(password, _DUMMY_PASSWORD_HASH)
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InvalidCredentialsError()

        return user

    def create_access_token_response(
        self,
        user: User,
        additional_claims: dict[str, Any] | None = None,
    ) -> TokenResponse:
        """Issue an access token and wrap it in the auth response schema.

        Behavior:
            1. Generates a JWT via :func:`backend.auth.jwt.create_access_token`
               using the user's stable token subject.
            2. Returns the existing :class:`TokenResponse` schema with the
               configured access-token lifetime in seconds.

        Args:
            user: The authenticated user to issue the token for.
            additional_claims: Optional custom claims forwarded to the JWT.

        Returns:
            A :class:`TokenResponse` carrying the signed access token.
        """
        access_token = create_access_token(
            subject=user.subject,
            additional_claims=additional_claims,
        )
        expires_in = (
            int(
                getattr(
                    settings,
                    "ACCESS_TOKEN_EXPIRE_MINUTES",
                    _DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
                )
            )
            * 60
        )
        return TokenResponse(access_token=access_token, expires_in=expires_in)


__all__ = [
    "AuthService",
    "AuthServiceError",
    "EmailAlreadyRegisteredError",
    "InvalidCredentialsError",
]
