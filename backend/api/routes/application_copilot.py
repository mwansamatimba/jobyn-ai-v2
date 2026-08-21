"""AI Application Copilot endpoint.

Thin HTTP layer — all business logic lives in
:class:`ApplicationCopilotOrchestrator`. This route:
  - Validates the request with Pydantic.
  - Resolves the authenticated user via JWT dependency.
  - Delegates to the service.
  - Maps service errors to HTTP responses.

No SQLAlchemy, no AI calls, no transaction management here.

Endpoint
--------
POST /application-copilot/generate
    Generate a tailored cover letter and application package for a job.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_session
from backend.models.career import CareerInsight
from backend.models.job import Job
from backend.models.resume import Resume
from backend.models.user import User
from backend.repositories.career import CareerInsightRepository
from backend.repositories.job import JobRepository
from backend.repositories.resume import ResumeRepository
from backend.schemas.application_copilot import (
    ApplicationCopilotRequest,
    ApplicationCopilotResponse,
)
from backend.services.application_copilot_service import (
    ApplicationCopilotOrchestrator,
    CopilotServiceError,
    JobNotFoundError,
    NoResumeError,
)

router = APIRouter(prefix="/application-copilot", tags=["Application Copilot"])
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Dependency                                                           #
# ------------------------------------------------------------------ #


def _get_copilot_service(
    session: AsyncSession = Depends(get_session),
) -> ApplicationCopilotOrchestrator:
    """Build a request-scoped ApplicationCopilotOrchestrator."""
    return ApplicationCopilotOrchestrator(
        job_repository=JobRepository(session=session, model=Job),
        resume_repository=ResumeRepository(session=session, model=Resume),
        insight_repository=CareerInsightRepository(
            session=session, model=CareerInsight
        ),
    )


# ------------------------------------------------------------------ #
# Generate endpoint                                                    #
# ------------------------------------------------------------------ #


@router.post(
    "/generate",
    response_model=ApplicationCopilotResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a tailored cover letter and application package",
)
async def generate_application(
    payload: ApplicationCopilotRequest,
    current_user: User = Depends(get_current_user),
    service: ApplicationCopilotOrchestrator = Depends(_get_copilot_service),
) -> ApplicationCopilotResponse:
    """Generate a tailored job application package for the authenticated user.

    Loads the user's latest parsed resume and any available career insight,
    then uses the AI copilot to produce a cover letter specific to the
    requested job.

    Raises:
        401: When the request is unauthenticated.
        404: When the job does not exist or the user has no parsed resume.
        502: When the AI generation service fails.
    """
    try:
        return await service.generate_for_user(
            user_id=current_user.id,
            job_id=payload.job_id,
            additional_context=payload.additional_context,
        )
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except NoResumeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except CopilotServiceError as exc:
        logger.exception(
            "Application copilot failed for user %s, job %s",
            current_user.id,
            payload.job_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
