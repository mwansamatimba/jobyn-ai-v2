"""Career navigator and skill gap analysis endpoints.

Thin HTTP layer — all business logic lives in :class:`CareerAnalysisService`.
Routes validate requests, delegate to the service, and map service errors to
HTTP responses. No SQLAlchemy, no AI calls, no transaction management here.

Endpoints
---------
POST /career/analyze   — Run AI career analysis against the caller's resume.
GET  /career/latest    — Return the caller's most recent career insight.
GET  /career/history   — Paginated list of the caller's career insights.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_session
from backend.models.career import CareerInsight
from backend.models.resume import Resume
from backend.models.user import User
from backend.repositories.career import CareerInsightRepository
from backend.repositories.resume import ResumeRepository
from backend.schemas.career import (
    CareerAnalysisResponse,
    CareerInsightListResponse,
    CareerInsightRead,
    CareerNavigatorRequest,
)
from backend.schemas.common import PaginatedResponse
from backend.services.career_service import (
    CareerAnalysisService,
    CareerServiceError,
    NoResumeError,
)

router = APIRouter(prefix="/career", tags=["Career"])
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Dependency                                                           #
# ------------------------------------------------------------------ #


def _get_career_service(
    session: AsyncSession = Depends(get_session),
) -> CareerAnalysisService:
    """Build a request-scoped CareerAnalysisService with its repositories."""
    return CareerAnalysisService(
        insight_repository=CareerInsightRepository(
            session=session, model=CareerInsight
        ),
        resume_repository=ResumeRepository(session=session, model=Resume),
    )


# ------------------------------------------------------------------ #
# Analyze endpoint                                                     #
# ------------------------------------------------------------------ #


@router.post(
    "/analyze",
    response_model=CareerAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run AI career analysis against the caller's resume",
)
async def analyze_career(
    payload: CareerNavigatorRequest,
    current_user: User = Depends(get_current_user),
    service: CareerAnalysisService = Depends(_get_career_service),
) -> CareerAnalysisResponse:
    """Analyse the authenticated user's career and generate an improvement roadmap.

    Uses the user's latest parsed resume. An optional ``target_role`` focuses
    the analysis on a specific career goal.

    Raises:
        404: When the user has no parsed resume.
        502: When the AI career navigator fails.
    """
    try:
        insight = await service.analyse_for_user(
            user_id=current_user.id,
            target_role=payload.target_role,
        )
    except NoResumeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except CareerServiceError as exc:
        logger.exception("Career analysis failed for user %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return CareerAnalysisResponse.from_insight(insight)


# ------------------------------------------------------------------ #
# Latest insight                                                       #
# ------------------------------------------------------------------ #


@router.get(
    "/latest",
    response_model=CareerInsightRead,
    summary="Return the caller's most recent career insight",
)
async def get_latest_insight(
    current_user: User = Depends(get_current_user),
    service: CareerAnalysisService = Depends(_get_career_service),
) -> CareerInsightRead:
    """Return the most recent persisted career analysis for the authenticated user.

    Raises:
        404: When no career analysis has been run yet.
    """
    insight = await service.get_latest_insight(current_user.id)
    if insight is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No career analysis found. Run POST /career/analyze first.",
        )
    return CareerInsightRead.model_validate(insight)


# ------------------------------------------------------------------ #
# History list                                                         #
# ------------------------------------------------------------------ #


@router.get(
    "/history",
    response_model=CareerInsightListResponse,
    summary="Return a paginated list of the caller's career insights",
)
async def list_insights(
    offset: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=20, ge=1, le=100, description="Maximum records to return"),
    current_user: User = Depends(get_current_user),
    service: CareerAnalysisService = Depends(_get_career_service),
) -> CareerInsightListResponse:
    """Return all stored career analyses for the authenticated user, newest first."""
    insights, total = await service.list_insights(
        current_user.id, offset=offset, limit=limit
    )
    return PaginatedResponse(
        items=[CareerInsightRead.model_validate(i) for i in insights],
        total=total,
        offset=offset,
        limit=limit,
    )
