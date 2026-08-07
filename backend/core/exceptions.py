"""Centralized application exceptions for Jobyn AI.

Framework-neutral exception classes shared across the domain, service and API
layers. These exceptions carry only a message and hold no HTTP status codes,
request context or framework logic so they stay importable from services and
from API-level exception handlers alike.
"""

from __future__ import annotations


class AppException(Exception):
    """Base class for all custom Jobyn AI exceptions.

    Provides a single common type for centralized exception handling and a
    normalized ``message`` attribute alongside the built-in ``args``.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvalidCredentialsError(AppException):
    """Raised when login credentials are incorrect.

    Deliberately identical for unknown email and wrong password so callers
    cannot determine whether an address is registered.
    """

    def __init__(
        self,
        message: str = "Invalid email or password.",
    ) -> None:
        super().__init__(message)


class InactiveUserError(AppException):
    """Raised when a valid account exists but is disabled or inactive."""

    def __init__(
        self,
        message: str = "This account is inactive.",
    ) -> None:
        super().__init__(message)


class EmailAlreadyRegisteredError(AppException):
    """Raised during registration when the email address is already in use."""

    def __init__(
        self,
        message: str = "An account with this email address already exists.",
    ) -> None:
        super().__init__(message)


class UserNotFoundError(AppException):
    """Raised when a requested user does not exist."""

    def __init__(
        self,
        message: str = "User not found.",
    ) -> None:
        super().__init__(message)


class PermissionDeniedError(AppException):
    """Raised when an authenticated user lacks permission for an action."""

    def __init__(
        self,
        message: str = "You do not have permission to perform this action.",
    ) -> None:
        super().__init__(message)


class ResourceNotFoundError(AppException):
    """Generic exception for a missing resource."""

    def __init__(
        self,
        message: str = "The requested resource was not found.",
    ) -> None:
        super().__init__(message)


class ValidationError(AppException):
    """Raised for domain-level validation failures."""

    def __init__(
        self,
        message: str = "The provided data is invalid.",
    ) -> None:
        super().__init__(message)