"""Job discovery, matching, and ingestion engine endpoints.

Thin HTTP layer — all business logic lives in service/repository layers.
Routes only validate requests, delegate, and map errors to HTTP responses.

Route order matters for FastAPI: named sub-paths must appear BEFORE /{job_id}
so that /search, /recent, /filters, /matches, /sync, /sync/status, /sources
are never captured by the UUID-parametrised route.

Endpoints
---------
POST /jobs/match              — AI matching against the caller's resume.
GET  /jobs                    — Paginated list + search/filter.
GET  /jobs/search             — Full-text + filter search (alias with params).
GET  /jobs/recent             — Most recently posted active jobs.
GET  /jobs/filters            — Available filter values for frontend dropdowns.
GET  /jobs/matches            — Caller's stored match results.
POST /jobs/deterministic-match — Deterministic scoring (no LLM).
POST /jobs/ingest             — Remotive ingestion (legacy dev/admin).
POST /jobs/sync               — Full multi-source ingestion sync.
GET  /jobs/sync/status        — Ingestion source registry state.
GET  /jobs/sources            — Configured ingestion sources.
GET  /jobs/{job_id}           — Single job detail.   ← MUST be last GET /{param}
POST /jobs                    — Create internal job posting.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

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
    DeterministicMatchResponse,
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


# ---------------------------------------------------------------------------
# Service dependency
# ---------------------------------------------------------------------------

def _get_job_service(session: AsyncSession = Depends(get_session)) -> JobDiscoveryService:
    return JobDiscoveryService(
        job_repository=JobRepository(session=session, model=Job),
        match_result_repository=MatchResultRepository(session=session, model=MatchResult),
        resume_repository=ResumeRepository(session=session, model=Resume),
    )


# ================================================================== #
# Named sub-paths  (ALL before /{job_id})                             #
# ================================================================== #

# ------------------------------------------------------------------
# AI match
# ------------------------------------------------------------------

@router.post(
    "/match",
    response_model=JobMatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Run AI job matching against the caller's resume",
)
async def match_jobs(
    resume_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> JobMatchResponse:
    try:
        return await service.match_for_user(user_id=current_user.id, resume_id=resume_id)
    except NoResumeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoJobsError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except JobServiceError as exc:
        logger.exception("Job matching failed for user %s", current_user.id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# ------------------------------------------------------------------
# Deterministic match
# ------------------------------------------------------------------

@router.post(
    "/deterministic-match",
    response_model=DeterministicMatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Rank jobs against the caller's resume (no LLM)",
)
async def deterministic_match_jobs(
    resume_id: uuid.UUID | None = Query(default=None),
    top_n: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> DeterministicMatchResponse:
    try:
        return await service.deterministic_match_for_user(
            user_id=current_user.id, resume_id=resume_id, top_n=top_n,
        )
    except NoResumeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoJobsError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Deterministic match failed for user %s", current_user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


# ------------------------------------------------------------------
# Stored match results
# ------------------------------------------------------------------

@router.get(
    "/matches",
    response_model=MatchListResponse,
    summary="Return the caller's stored match results",
)
async def list_matches(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    matcher_type: Literal["deterministic", "ai"] | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> MatchListResponse:
    matches, total = await service.get_user_matches(
        current_user.id, offset=offset, limit=limit, matcher_type=matcher_type
    )
    return PaginatedResponse(
        items=[MatchResultRead.model_validate(m) for m in matches],
        total=total, offset=offset, limit=limit,
    )


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

@router.get(
    "/search",
    response_model=JobListResponse,
    summary="Full-text and filter job search",
)
async def search_jobs(
    q: str | None = Query(default=None, description="Free-text (title, company, description)"),
    location: str | None = Query(default=None),
    province: str | None = Query(default=None, description="Zambia province filter"),
    country: str | None = Query(default=None),
    remote: bool | None = Query(default=None, description="true = remote roles only"),
    employment_type: str | None = Query(default=None),
    seniority: str | None = Query(default=None),
    category: str | None = Query(default=None),
    company: str | None = Query(default=None),
    source: str | None = Query(default=None, description="e.g. greenhouse, reliefweb"),
    date_posted: str | None = Query(default=None, description="ISO date — posted on or after"),
    deadline: str | None = Query(default=None, description="ISO date — deadline on or after"),
    sort: str | None = Query(default="recent", description="recent | deadline | company"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    from backend.services.ingestion_query import search_jobs_query
    offset = (page - 1) * limit
    jobs, total = await search_jobs_query(
        session, q=q, location=location, province=province, country=country,
        remote=remote, employment_type=employment_type, seniority=seniority,
        category=category, company=company, source=source,
        date_posted=date_posted, deadline=deadline, sort=sort or "recent",
        offset=offset, limit=limit,
    )
    return JobListResponse(
        items=[JobRead.model_validate(j) for j in jobs],
        total=total, offset=offset, limit=limit,
    )


# ------------------------------------------------------------------
# Recent
# ------------------------------------------------------------------

@router.get(
    "/recent",
    response_model=JobListResponse,
    summary="Most recently posted active jobs",
)
async def recent_jobs(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    service: JobDiscoveryService = Depends(_get_job_service),
) -> JobListResponse:
    jobs, total = await service.list_jobs(offset=0, limit=limit)
    return JobListResponse(
        items=[JobRead.model_validate(j) for j in jobs],
        total=total, offset=0, limit=limit,
    )


# ------------------------------------------------------------------
# Filters
# ------------------------------------------------------------------

@router.get(
    "/filters",
    summary="Available filter values for frontend dropdowns",
)
async def job_filters(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from backend.services.ingestion_query import get_filter_options
    return await get_filter_options(session)


# ------------------------------------------------------------------
# Remotive ingest (legacy dev/admin)
# ------------------------------------------------------------------

@router.post(
    "/ingest",
    status_code=status.HTTP_200_OK,
    summary="Trigger legacy Remotive ingestion (dev/admin)",
)
async def trigger_ingest(
    category: str = Query(default="software-dev"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
) -> dict:
    from backend.services.job_ingestion import ingest_jobs
    result = await ingest_jobs(category=category, limit=limit)
    return result.to_dict()


# ------------------------------------------------------------------
# Multi-source sync
# ------------------------------------------------------------------

@router.post(
    "/sync",
    status_code=status.HTTP_200_OK,
    summary="Trigger full ingestion sync from all configured sources",
)
async def trigger_sync(
    sources: str | None = Query(
        default=None,
        description="Comma-separated source names, or omit for all.",
    ),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from backend.ingestion.sync import run_sync
    source_list: list[str] | None = None
    if sources:
        source_list = [s.strip() for s in sources.split(",") if s.strip()]
    result = await run_sync(session, source_names=source_list)
    return result.to_dict()


# ------------------------------------------------------------------
# Sync status
# ------------------------------------------------------------------

@router.get(
    "/sync/status",
    summary="Ingestion source registry state",
)
async def sync_status(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from sqlalchemy import select as _select
    from backend.models.ingestion import IngestionSource
    result = await session.execute(_select(IngestionSource).order_by(IngestionSource.name))
    sources = result.scalars().all()
    return {
        "sources": [
            {
                "name": s.name,
                "active": s.active,
                "permission_status": str(s.permission_status),
                "last_successful_sync": s.last_successful_sync.isoformat() if s.last_successful_sync else None,
                "last_attempted_sync": s.last_attempted_sync.isoformat() if s.last_attempted_sync else None,
                "last_error": s.last_error,
            }
            for s in sources
        ]
    }


# ------------------------------------------------------------------
# Sources list
# ------------------------------------------------------------------

@router.get(
    "/sources",
    summary="List configured ingestion sources",
)
async def list_sources(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from sqlalchemy import select as _select
    from backend.models.ingestion import IngestionSource
    result = await session.execute(_select(IngestionSource).order_by(IngestionSource.name))
    sources = result.scalars().all()
    return {
        "sources": [
            {
                "id": str(s.id),
                "name": s.name,
                "organization_name": s.organization_name,
                "source_type": str(s.source_type),
                "permission_status": str(s.permission_status),
                "active": s.active,
                "crawl_interval_minutes": s.crawl_interval_minutes,
                "last_successful_sync": s.last_successful_sync.isoformat() if s.last_successful_sync else None,
            }
            for s in sources
        ]
    }


# ================================================================== #
# Parametrised routes — MUST come after all named sub-paths           #
# ================================================================== #

@router.get(
    "",
    response_model=JobListResponse,
    summary="List active job postings (supports all search/filter params)",
)
async def list_jobs(
    # Search / filter params (mirrors /search for frontend convenience)
    q: str | None = Query(default=None, description="Free-text search"),
    location: str | None = Query(default=None),
    province: str | None = Query(default=None),
    country: str | None = Query(default=None),
    remote: bool | None = Query(default=None),
    employment_type: str | None = Query(default=None),
    seniority: str | None = Query(default=None),
    category: str | None = Query(default=None),
    company: str | None = Query(default=None),
    source: str | None = Query(default=None),
    date_posted: str | None = Query(default=None),
    deadline: str | None = Query(default=None),
    sort: str | None = Query(default="recent"),
    page: int = Query(default=1, ge=1),
    # Keep backward-compatible offset/limit as well
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    """Return a paginated list of active job postings.

    Supports the same filter parameters as /jobs/search.
    When any filter is supplied, delegates to the full search query.
    With no filters, returns the most recent jobs.
    """
    # If page > 1 use page-based offset; otherwise honour explicit offset
    effective_offset = (page - 1) * limit if page > 1 else offset

    from backend.services.ingestion_query import search_jobs_query
    jobs, total = await search_jobs_query(
        session, q=q, location=location, province=province, country=country,
        remote=remote, employment_type=employment_type, seniority=seniority,
        category=category, company=company, source=source,
        date_posted=date_posted, deadline=deadline, sort=sort or "recent",
        offset=effective_offset, limit=limit,
    )

    return JobListResponse(
        items=[JobRead.model_validate(j) for j in jobs],
        total=total, offset=effective_offset, limit=limit,
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
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return JobRead.model_validate(job)


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
    job = await service.create_job(
        **payload.model_dump(exclude_none=True),
        created_by_user_id=current_user.id,
    )
    return JobRead.model_validate(job)
