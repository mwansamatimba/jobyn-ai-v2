"""Application tracking service.

Owns all business logic for the Application lifecycle:
  - Creating an application (with duplicate-prevention).
  - Retrieving a single application (with ownership enforcement).
  - Listing and paginating a user's applications.
  - Updating permitted fields, including status transitions.
  - Deleting an application.

Status transition rules
-----------------------
The ``ApplicationStatus`` enum defines:
    draft → applied → under_review → interviewing → offered
                                                  ↘ rejected
    Any status → withdrawn  (candidate-initiated exit)
    rejected and offered are terminal — no further transitions.

These rules reflect the real-world hiring pipeline and prevent nonsensical
backwards transitions. They live here, not in routes.

Transaction ownership
---------------------
All commits happen in this service. Routes and repositories never commit.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy.exc import IntegrityError

from backend.models.enums import ApplicationStatus
from backend.models.job import Application
from backend.repositories.application import ApplicationRepository
from backend.repositories.job import JobRepository
from backend.services.base import BaseService

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Status transition map                                                #
# ------------------------------------------------------------------ #

# Maps each status to the set of statuses it may legally transition to.
_ALLOWED_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: frozenset({
        ApplicationStatus.APPLIED,
        ApplicationStatus.WITHDRAWN,
    }),
    ApplicationStatus.APPLIED: frozenset({
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    }),
    ApplicationStatus.UNDER_REVIEW: frozenset({
        ApplicationStatus.INTERVIEWING,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    }),
    ApplicationStatus.INTERVIEWING: frozenset({
        ApplicationStatus.OFFERED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    }),
    # Terminal statuses — no further transitions allowed.
    ApplicationStatus.OFFERED: frozenset(),
    ApplicationStatus.REJECTED: frozenset(),
    ApplicationStatus.WITHDRAWN: frozenset(),
}

_INITIAL_ALLOWED = frozenset({ApplicationStatus.DRAFT, ApplicationStatus.APPLIED})


# ------------------------------------------------------------------ #
# Exceptions                                                           #
# ------------------------------------------------------------------ #


class ApplicationServiceError(Exception):
    """Raised when an application operation fails."""


class ApplicationNotFoundError(ApplicationServiceError):
    """Raised when a requested application does not exist or is not owned by the user."""


class DuplicateApplicationError(ApplicationServiceError):
    """Raised when the user already has an application for the same job."""


class JobNotFoundError(ApplicationServiceError):
    """Raised when the target job does not exist."""


class InvalidStatusError(ApplicationServiceError):
    """Raised when a requested status value is invalid."""


class InvalidTransitionError(ApplicationServiceError):
    """Raised when a status transition is not permitted by the lifecycle rules."""


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #


def _parse_status(raw: str | None, default: ApplicationStatus) -> ApplicationStatus:
    """Parse a string into an ApplicationStatus, raising InvalidStatusError on failure."""
    if raw is None:
        return default
    try:
        return ApplicationStatus(raw)
    except ValueError:
        valid = ", ".join(s.value for s in ApplicationStatus)
        raise InvalidStatusError(
            f"'{raw}' is not a valid application status. Valid values: {valid}."
        )


# ------------------------------------------------------------------ #
# Service                                                              #
# ------------------------------------------------------------------ #


class ApplicationService(BaseService[ApplicationRepository]):
    """Orchestrates the full Application lifecycle for an authenticated user."""

    def __init__(
        self,
        application_repository: ApplicationRepository,
        job_repository: JobRepository,
    ) -> None:
        super().__init__(application_repository)
        self.application_repository = application_repository
        self.job_repository = job_repository

    async def create_application(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        cover_letter: str | None = None,
        notes: str | None = None,
        status_str: str | None = None,
    ) -> Application:
        """Create a new application for the authenticated user.

        Args:
            user_id: JWT-derived user UUID — never from request body.
            job_id: UUID of the target job.
            cover_letter: Optional cover letter text.
            notes: Optional private notes.
            status_str: Initial status string; defaults to ``draft``.

        Returns:
            The persisted :class:`Application` record.

        Raises:
            JobNotFoundError: When the job does not exist.
            DuplicateApplicationError: When the user already applied to this job.
            InvalidStatusError: When the status string is not a valid status.
            InvalidTransitionError: When the requested initial status is not allowed.
        """
        # Validate the job exists.
        job = await self.job_repository.get_by_id(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found.")

        # Parse and validate the initial status.
        initial_status = _parse_status(status_str, ApplicationStatus.DRAFT)
        if initial_status not in _INITIAL_ALLOWED:
            valid = ", ".join(s.value for s in _INITIAL_ALLOWED)
            raise InvalidTransitionError(
                f"Cannot create an application with status '{initial_status}'. "
                f"Allowed initial statuses: {valid}."
            )

        # Check for an existing application (enforce unique constraint at app level).
        existing = await self.application_repository.get_existing_application(
            user_id, job_id
        )
        if existing is not None:
            raise DuplicateApplicationError(
                f"You already have an application for job {job_id}."
            )

        # Create the record.
        try:
            application = await self.application_repository.create(
                user_id=user_id,
                job_id=job_id,
                status=initial_status,
                cover_letter=cover_letter,
                notes=notes,
                applied_at=date.today() if initial_status == ApplicationStatus.APPLIED else None,
            )
            await self.commit()
        except IntegrityError:
            await self.rollback()
            raise DuplicateApplicationError(
                f"You already have an application for job {job_id}."
            )

        return application

    async def get_application(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
    ) -> Application:
        """Return a single application owned by the user.

        Args:
            user_id: JWT-derived user UUID.
            application_id: UUID of the application.

        Returns:
            The :class:`Application` record.

        Raises:
            ApplicationNotFoundError: When not found or not owned by the user.
        """
        application = await self.application_repository.get_user_application(
            user_id, application_id
        )
        if application is None:
            raise ApplicationNotFoundError(
                f"Application {application_id} not found."
            )
        return application

    async def list_applications(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
        status_str: str | None = None,
        job_id: uuid.UUID | None = None,
    ) -> tuple[list[Application], int]:
        """Return a paginated list of applications for the user.

        Args:
            user_id: JWT-derived user UUID.
            offset: Number of records to skip.
            limit: Maximum records to return.
            status_str: Optional status filter string.
            job_id: Optional job filter.

        Returns:
            A tuple of (applications list, total count).

        Raises:
            InvalidStatusError: When the status filter string is invalid.
        """
        status_filter = _parse_status(status_str, None) if status_str else None  # type: ignore[arg-type]

        applications = await self.application_repository.list_for_user(
            user_id,
            offset=offset,
            limit=limit,
            status=status_filter,
            job_id=job_id,
        )
        total = await self.application_repository.count_for_user(
            user_id,
            status=status_filter,
            job_id=job_id,
        )
        return applications, total

    async def update_application(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        status_str: str | None = None,
        cover_letter: str | None = None,
        notes: str | None = None,
        applied_at: date | None = None,
        last_activity_at: date | None = None,
    ) -> Application:
        """Update permitted fields on an application.

        Status transitions are validated against the lifecycle rules. Other
        fields are applied unconditionally when present.

        Args:
            user_id: JWT-derived user UUID.
            application_id: UUID of the application to update.
            status_str: New status string (optional).
            cover_letter: New cover letter text (optional).
            notes: New notes text (optional).
            applied_at: Date applied externally (optional).
            last_activity_at: Date of last activity (optional).

        Returns:
            The updated :class:`Application` record.

        Raises:
            ApplicationNotFoundError: When not found or not owned by the user.
            InvalidStatusError: When the new status string is invalid.
            InvalidTransitionError: When the transition is not permitted.
        """
        application = await self.application_repository.get_user_application(
            user_id, application_id
        )
        if application is None:
            raise ApplicationNotFoundError(
                f"Application {application_id} not found."
            )

        updates: dict = {}

        # Validate and apply status transition.
        if status_str is not None:
            new_status = _parse_status(status_str, application.status)
            if new_status != application.status:
                allowed = _ALLOWED_TRANSITIONS.get(application.status, frozenset())
                if new_status not in allowed:
                    raise InvalidTransitionError(
                        f"Cannot transition from '{application.status}' "
                        f"to '{new_status}'. "
                        f"Allowed: {', '.join(s.value for s in allowed) or 'none'}."
                    )
                updates["status"] = new_status
                # Auto-stamp applied_at when transitioning to APPLIED.
                if new_status == ApplicationStatus.APPLIED and application.applied_at is None:
                    updates["applied_at"] = date.today()

        # Apply content fields if provided (exclude_unset pattern).
        if cover_letter is not None:
            updates["cover_letter"] = cover_letter
        if notes is not None:
            updates["notes"] = notes
        if applied_at is not None:
            updates["applied_at"] = applied_at
        if last_activity_at is not None:
            updates["last_activity_at"] = last_activity_at

        if updates:
            application = await self.application_repository.update(
                application, **updates
            )
            await self.commit()
            # Refresh to materialise server-side onupdate values (e.g. updated_at)
            # so Pydantic can read them outside the async context.
            await self.application_repository.session.refresh(application)

        return application

    async def delete_application(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
    ) -> None:
        """Delete an application owned by the user.

        The ``Application`` model does not use ``SoftDeleteMixin`` so this
        performs a physical delete via :meth:`BaseRepository.delete`.

        Args:
            user_id: JWT-derived user UUID.
            application_id: UUID of the application to delete.

        Raises:
            ApplicationNotFoundError: When not found or not owned by the user.
        """
        application = await self.application_repository.get_user_application(
            user_id, application_id
        )
        if application is None:
            raise ApplicationNotFoundError(
                f"Application {application_id} not found."
            )
        await self.application_repository.delete(application)
        await self.commit()


__all__ = [
    "ApplicationService",
    "ApplicationServiceError",
    "ApplicationNotFoundError",
    "DuplicateApplicationError",
    "JobNotFoundError",
    "InvalidStatusError",
    "InvalidTransitionError",
]
