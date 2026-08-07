"""Prototype API endpoints for the Jobyn AI Google Prototype MVP demo.

Thin HTTP layer exposing the AI MVP services. Each endpoint validates the
request payload, delegates to the corresponding AI service and maps
service-level failures to HTTP 400 responses without leaking Gemini internals.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.ai.candidate_profile import (
    CandidateProfileError,
    CandidateProfileService,
)
from backend.ai.career_navigator import (
    CareerNavigatorError,
    CareerNavigatorService,
)
from backend.ai.cv_analyzer import CVAnalysisError, CVAnalyzerService
from backend.ai.interview_coach import (
    InterviewCoachError,
    InterviewCoachService,
)
from backend.ai.job_matcher import JobMatcherError, JobMatcherService

router = APIRouter(
    prefix="/prototype",
    tags=["Prototype"],
)

logger = logging.getLogger(__name__)

_cv_analyzer = CVAnalyzerService()
_profile_generator = CandidateProfileService()
_career_navigator = CareerNavigatorService()
_job_matcher = JobMatcherService()
_interview_coach = InterviewCoachService()


class AnalyzeCVRequest(BaseModel):
    """Payload for CV analysis."""

    resume_text: str = Field(min_length=1)


class ProfileRequest(BaseModel):
    """Payload for candidate profile generation."""

    cv_analysis: dict[str, Any]


class NavigateRequest(BaseModel):
    """Payload for career navigation."""

    candidate_profile: dict[str, Any]


class MatchJobsRequest(BaseModel):
    """Payload for job matching."""

    candidate_profile: dict[str, Any]
    available_jobs: list[dict[str, Any]]


class InterviewCoachRequest(BaseModel):
    """Payload for interview preparation."""

    candidate_profile: dict[str, Any]
    target_role: str = Field(min_length=1)


@router.post("/analyze-cv", summary="Analyze a CV from resume text")
async def analyze_cv(payload: AnalyzeCVRequest) -> dict[str, Any]:
    """Return AI candidate analysis for the supplied resume text.

    Args:
        payload: The request body containing the resume text.

    Returns:
        A structured CV analysis dictionary.
    """
    try:
        return await _cv_analyzer.analyze_cv(payload.resume_text)
    except CVAnalysisError as exc:
        logger.exception("CV analysis failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/profile", summary="Generate a polished candidate profile")
async def generate_profile(payload: ProfileRequest) -> dict[str, Any]:
    """Generate a candidate profile from CV analysis output.

    Args:
        payload: The request body containing the CV analysis.

    Returns:
        A candidate profile dictionary.
    """
    try:
        return await _profile_generator.generate_profile(payload.cv_analysis)
    except CandidateProfileError as exc:
        logger.exception("Candidate profile generation failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/navigate", summary="Generate AI career navigation guidance")
async def navigate(payload: NavigateRequest) -> dict[str, Any]:
    """Generate a career roadmap for a candidate profile.

    Args:
        payload: The request body containing the candidate profile.

    Returns:
        A career navigation dictionary.
    """
    try:
        return await _career_navigator.navigate(payload.candidate_profile)
    except CareerNavigatorError as exc:
        logger.exception("Career navigation failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/match-jobs", summary="Match a candidate against available jobs")
async def match_jobs(payload: MatchJobsRequest) -> dict[str, Any]:
    """Return ranked job matches for a candidate profile.

    Args:
        payload: The request body containing the candidate profile and jobs.

    Returns:
        A job matching dictionary.
    """
    try:
        return await _job_matcher.match_jobs(
            payload.candidate_profile,
            payload.available_jobs,
        )
    except JobMatcherError as exc:
        logger.exception("Job matching failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/interview-coach", summary="Generate interview preparation")
async def generate_interview_plan(payload: InterviewCoachRequest) -> dict[str, Any]:
    """Generate an interview preparation plan for a target role.

    Args:
        payload: The request body containing the candidate profile and role.

    Returns:
        An interview preparation dictionary.
    """
    try:
        return await _interview_coach.generate_interview_plan(
            payload.candidate_profile,
            payload.target_role,
        )
    except InterviewCoachError as exc:
        logger.exception("Interview coaching failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
