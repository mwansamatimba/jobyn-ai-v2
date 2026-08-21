"""Application tracking endpoints.

Thin HTTP layer — all business logic lives in :class:`ApplicationService`.
Routes validate requests, call the service, and map service exceptions to
HTTP responses. No SQLAlchemy, no AI calls, no transaction management here.

Endpoints
---------
POST   /applications                     Create a new application.
GET    /applications                     List the caller's applications.
GET    /applications/{application_id}    Return one application.
PATCH  /applications/{application_id}    Update permitted fields / status.
DELETE /applications/{application_id}    Delete an application.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_session
from backend.models.enums import ApplicationStatus
from backend.models.job import Application, Job
from backend.models.user import User
from backend.repositories.application import ApplicationRepository
from backend.repositories.job import JobRepository
from backend.schemas.application import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationRead,
    ApplicationUpdate,
)
from backend.schemas.common import PaginatedResponse
from backend.models.resume import Resume
from backend.repositories.resume import ResumeRepository
from backend.schemas.interview import InterviewPrepResponse
from backend.services.application_service import (
    ApplicationNotFoundError,
    ApplicationService,
    ApplicationServiceError,
    DuplicateApplicationError,
    InvalidStatusError,
    InvalidTransitionError,
    JobNotFoundError,
)
from backend.services.interview_service import (
    ApplicationNotFoundError as InterviewAppNotFoundError,
    InterviewPreparationService,
    InterviewServiceError,
    JobNotFoundError as InterviewJobNotFoundError,
    NoResumeError,
)

router = APIRouter(prefix="/applications", tags=["Applications"])
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Dependency                                                           #
# ------------------------------------------------------------------ #


def _get_application_service(
    session: AsyncSession = Depends(get_session),
) -> ApplicationService:
    """Build a request-scoped ApplicationService with its repositories."""
    return ApplicationService(
        application_repository=ApplicationRepository(
            session=session, model=Application
        ),
        job_repository=JobRepository(session=session, model=Job),
    )


# ------------------------------------------------------------------ #
# Create                                                               #
# ------------------------------------------------------------------ #


@router.post(
    "",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new job application",
)
async def create_application(
    payload: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    service: ApplicationService = Depends(_get_application_service),
) -> ApplicationRead:
    """Create a new application for the authenticated user.

    Raises:
        404: When the target job does not exist.
        409: When the user already has an application for this job.
        422: When the status value or transition is invalid.
    """
    try:
        application = await service.create_application(
            user_id=current_user.id,
            job_id=payload.job_id,
            cover_letter=payload.cover_letter,
            notes=payload.notes,
            status_str=payload.status,
        )
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DuplicateApplicationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (InvalidStatusError, InvalidTransitionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ApplicationRead.model_validate(application)


# ------------------------------------------------------------------ #
# List                                                                 #
# ------------------------------------------------------------------ #


@router.get(
    "",
    response_model=ApplicationListResponse,
    summary="List the caller's job applications",
)
async def list_applications(
    offset: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum records to return"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by application status.",
    ),
    job_id: uuid.UUID | None = Query(
        default=None,
        description="Filter by job UUID.",
    ),
    current_user: User = Depends(get_current_user),
    service: ApplicationService = Depends(_get_application_service),
) -> ApplicationListResponse:
    """Return a paginated list of the authenticated user's applications."""
    try:
        applications, total = await service.list_applications(
            current_user.id,
            offset=offset,
            limit=limit,
            status_str=status_filter,
            job_id=job_id,
        )
    except InvalidStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return PaginatedResponse(
        items=[ApplicationRead.model_validate(a) for a in applications],
        total=total,
        offset=offset,
        limit=limit,
    )


# ------------------------------------------------------------------ #
# Get one                                                              #
# ------------------------------------------------------------------ #


@router.get(
    "/{application_id}",
    response_model=ApplicationRead,
    summary="Return a single application",
)
async def get_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ApplicationService = Depends(_get_application_service),
) -> ApplicationRead:
    """Return the detail of a single application owned by the caller.

    Returns 404 for applications belonging to other users so resource
    existence is not disclosed.
    """
    try:
        application = await service.get_application(current_user.id, application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return ApplicationRead.model_validate(application)


# ------------------------------------------------------------------ #
# Update                                                               #
# ------------------------------------------------------------------ #


@router.patch(
    "/{application_id}",
    response_model=ApplicationRead,
    summary="Update an application's status or fields",
)
async def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    service: ApplicationService = Depends(_get_application_service),
) -> ApplicationRead:
    """Update permitted fields on an application.

    Only fields included in the request body are changed. Status changes
    are validated against the application lifecycle rules.

    Raises:
        404: When the application does not exist or belongs to another user.
        422: When the status value is invalid or the transition is forbidden.
    """
    try:
        application = await service.update_application(
            current_user.id,
            application_id,
            status_str=payload.status,
            cover_letter=payload.cover_letter,
            notes=payload.notes,
            applied_at=payload.applied_at,
            last_activity_at=payload.last_activity_at,
        )
    except ApplicationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (InvalidStatusError, InvalidTransitionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ApplicationRead.model_validate(application)


# ------------------------------------------------------------------ #
# Delete                                                               #
# ------------------------------------------------------------------ #


@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an application",
)
async def delete_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ApplicationService = Depends(_get_application_service),
) -> None:
    """Delete an application owned by the caller.

    Returns 404 for applications belonging to other users.
    """
    try:
        await service.delete_application(current_user.id, application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


# ------------------------------------------------------------------ #
# Interview preparation                                                #
# ------------------------------------------------------------------ #


def _get_interview_service(
    session: AsyncSession = Depends(get_session),
) -> InterviewPreparationService:
    """Build a request-scoped InterviewPreparationService."""
    return InterviewPreparationService(
        application_repository=ApplicationRepository(
            session=session, model=Application
        ),
        job_repository=JobRepository(session=session, model=Job),
        resume_repository=ResumeRepository(session=session, model=Resume),
    )


@router.post(
    "/{application_id}/interview-prep",
    response_model=InterviewPrepResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate interview preparation for an application",
)
async def generate_interview_prep(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: InterviewPreparationService = Depends(_get_interview_service),
) -> InterviewPrepResponse:
    """Generate a tailored interview preparation plan for the authenticated user.

    Uses the job title from the application as the target role and the user's
    latest parsed resume as the candidate profile.

    Raises:
        401: Unauthenticated.
        404: Application not found or belongs to another user.
        404: Associated job no longer exists.
        404: User has no parsed resume.
        502: AI generation failed.
    """
    try:
        plan = await service.generate_for_application(
            user_id=current_user.id,
            application_id=application_id,
        )
    except (InterviewAppNotFoundError, InterviewJobNotFoundError, NoResumeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InterviewServiceError as exc:
        logger.exception(
            "Interview prep failed for user %s, application %s",
            current_user.id,
            application_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return InterviewPrepResponse(**plan)
