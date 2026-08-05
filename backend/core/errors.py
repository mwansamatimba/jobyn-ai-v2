"""Domain exceptions and centralized HTTP error mapping.

Services and repositories raise :class:`JobynError` subclasses instead of
returning HTTP-specific errors. The exception handlers registered here are the
only place that translates domain errors into HTTP responses, which prevents
duplicated error-handling business logic across endpoints.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class JobynError(Exception):
    """Base class for all application-level errors."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred"

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class NotFoundError(JobynError):
    status_code = 404
    code = "not_found"
    message = "Resource not found"


class ConflictError(JobynError):
    status_code = 409
    code = "conflict"
    message = "Resource already exists or is in conflict"


class AuthenticationError(JobynError):
    status_code = 401
    code = "authentication_failed"
    message = "Authentication required or credentials are invalid"


class AuthorizationError(JobynError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action"


class UnprocessableEntityError(JobynError):
    status_code = 422
    code = "unprocessable_entity"
    message = "The request could not be processed"


class RateLimitError(JobynError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests"


def _error_body(exc: JobynError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        body["details"] = exc.details
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach JSON error handlers to the given FastAPI instance."""

    @app.exception_handler(JobynError)
    async def _handle_jobyn_error(request: Request, exc: JobynError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_error_body(JobynError()),
        )
