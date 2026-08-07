"""Resume upload and candidate profile endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from backend.api.deps import get_current_user, get_session
from backend.models.resume import Resume, UploadedResume
from backend.models.user import User
from backend.repositories.resume import ResumeRepository, UploadedResumeRepository
from backend.schemas.resume import CandidateProfileRead, ParsedResumeRead
from backend.services.resume_service import ResumeService, ResumeUploadError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/resume", tags=["Resume"])

logger = logging.getLogger(__name__)


def _get_resume_service(session: AsyncSession = Depends(get_session)) -> ResumeService:
    return ResumeService(
        upload_repository=UploadedResumeRepository(session=session, model=UploadedResume),
        resume_repository=ResumeRepository(session=session, model=Resume),
    )


@router.post(
    "/upload",
    response_model=ParsedResumeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse a resume file (PDF or DOCX)",
)
async def upload_resume(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    service: ResumeService = Depends(_get_resume_service),
) -> ParsedResumeRead:
    """Upload a CV file, extract its text, and run AI parsing.

    Accepts PDF or DOCX. Maximum file size is 10 MB.

    Returns the upload record with the AI-parsed structured data attached.
    """
    content_type = file.content_type or ""
    filename = file.filename or "resume"
    file_bytes = await file.read()

    try:
        upload = await service.upload_and_parse(
            user_id=current_user.id,
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )
    except ResumeUploadError as exc:
        logger.info("Resume upload failed for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ParsedResumeRead.model_validate(upload)


@router.get(
    "/profile/{candidate_id}",
    response_model=CandidateProfileRead,
    summary="Return the parsed candidate profile for a user",
)
async def get_candidate_profile(
    candidate_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: ResumeService = Depends(_get_resume_service),
) -> CandidateProfileRead:
    """Return the latest AI-generated resume profile for the given candidate.

    Any authenticated user may query a profile by UUID. Returns 404 when no
    parsed resume exists for the candidate.
    """
    resume = await service.get_candidate_profile(candidate_id)
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No parsed resume found for this candidate.",
        )
    return CandidateProfileRead.model_validate(resume)
