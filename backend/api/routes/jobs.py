"""Job discovery and matching endpoints.

Thin HTTP layer — all business logic lives in :class:`JobDiscoveryService`.
Routes only validate requests, delegate to the service, and map service errors
to HTTP responses. No SQLAlchemy, no AI calls, no transaction management here.

Endpoints
---------
POST /jobs/match      — Run AI matching against the caller's latest resume.
GET  /jobs            — Paginated list of active jobs.
GET  /jobs/matches    — The caller's stored match results.
GET  /jobs/{job_id}   — Single job detail.
POST /jobs            — Create an internal job posting.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_session
from backend.models.job import Job, MatchResult
from backend.models.resume import Resume
from backend.models.user import User
from backend.repositories.job import JobRepository, MatchResultRepository
from backend.repositories.resume import ResumeRepository
from backend.schemas.common import PaginatedResponse
from backend.schemas.job import (
    JobCreate,
    JobListResponse,
    JobMatchResponse,
    JobRead,
    MatchListResponse,
    MatchResultRead,
)
from backend.services.job_service import (
    JobDiscoveryService,
    JobServiceError,
    NoJobsError,
    NoResumeError,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Dependency                                                           #
# ------------------------------------------------------------------ #


def _get_job_service(session: AsyncSession = Depends(get_session)) -> JobDiscoveryService:
    """Build a request-scoped JobDiscoveryService with all its repositories."""
    return JobDiscoveryService(
        job_repository=JobRepository(session=session, model=Job),
        match_result_repository=MatchResultRepository(session=session, model=MatchResult),
        resume_repository=ResumeRepository(session=session, model=Resume),
    )


# ------------------------------------------------------------------ #
# Match endpoint                                                       #
# ------------------------------------------------------------------ #


@router.post(
    "/match",
    response_model=JobMatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Run AI job matching against the caller's resume",
)
async def match_jobs(
    resume_id: uuid.UUID | None = Query(
        default=None,
        description="Optional: specific resume UUID to match against. "
        "Defaults to the user's latest resume.",
    ),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> JobMatchResponse:
    """Return AI-ranked job recommendations for the authenticated user.

    Uses the user's latest parsed resume unless ``resume_id`` is specified.
    Persists each match to the ``match_results`` table before returning.

    Raises:
        404: When the user has no parsed resume.
        404: When there are no active jobs in the database.
        502: When the AI matching service fails.
    """
    try:
        return await service.match_for_user(
            user_id=current_user.id,
            resume_id=resume_id,
        )
    except NoResumeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except NoJobsError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except JobServiceError as exc:
        logger.exception("Job matching failed for user %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


# ------------------------------------------------------------------ #
# Job list and detail                                                  #
# ------------------------------------------------------------------ #


@router.get(
    "",
    response_model=JobListResponse,
    summary="List active job postings",
)
async def list_jobs(
    offset: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum records to return"),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> JobListResponse:
    """Return a paginated list of active job postings.

    Authentication is required so that only registered users browse the job
    board.
    """
    jobs, total = await service.list_jobs(offset=offset, limit=limit)
    return PaginatedResponse(
        items=[JobRead.model_validate(j) for j in jobs],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/matches",
    response_model=MatchListResponse,
    summary="Return the caller's stored match results",
)
async def list_matches(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> MatchListResponse:
    """Return the authenticated user's previously computed match results."""
    matches, total = await service.get_user_matches(
        current_user.id, offset=offset, limit=limit
    )
    return PaginatedResponse(
        items=[MatchResultRead.model_validate(m) for m in matches],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{job_id}",
    response_model=JobRead,
    summary="Return a single job posting by id",
)
async def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> JobRead:
    """Return the detail of a single active job posting.

    Raises:
        404: When the job does not exist or has been soft-deleted.
    """
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )
    return JobRead.model_validate(job)


# ------------------------------------------------------------------ #
# Job creation (internal postings)                                     #
# ------------------------------------------------------------------ #


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an internal job posting",
)
async def create_job(
    payload: JobCreate,
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> JobRead:
    """Create a new internal job posting owned by the authenticated user.

    Any authenticated user may create jobs in this MVP. Role-based access
    control can be layered in without changing the route signature.
    """
    job = await service.create_job(
        **payload.model_dump(exclude_none=True),
        created_by_user_id=current_user.id,
    )
    return JobRead.model_validate(job)
