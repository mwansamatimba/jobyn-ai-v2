"""Job discovery and matching service.

Owns the full job-matching orchestration pipeline:
  1. Load the user's latest completed Resume from the database.
  2. Fetch active jobs from the database via JobRepository.
  3. Convert ORM objects into plain dicts for the AI layer.
  4. Call the stateless JobMatcherService.
  5. Persist each top match as a MatchResult record.
  6. Return a structured JobMatchResponse.

All transaction boundaries are managed here. Routes stay free of business
logic and session lifecycle concerns, following the same pattern as
ResumeService.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from backend.ai.job_matcher import JobMatcherError, JobMatcherService
from backend.models.job import Job, MatchResult
from backend.models.resume import Resume
from backend.repositories.job import JobRepository, MatchResultRepository
from backend.repositories.resume import ResumeRepository
from backend.schemas.job import JobMatchItem, JobMatchResponse
from backend.services.base import BaseService

logger = logging.getLogger(__name__)

_MATCH_STATUS_COMPLETED = "completed"


class JobServiceError(Exception):
    """Raised when the job service pipeline fails."""


class NoResumeError(JobServiceError):
    """Raised when the user has no completed resume to match against."""


class NoJobsError(JobServiceError):
    """Raised when there are no active jobs to match against."""


def _job_to_dict(job: Job) -> dict[str, Any]:
    """Convert a Job ORM instance to an AI-friendly plain dict."""
    return {
        "job_id": str(job.id),
        "job_title": job.title,
        "company": job.company_name,
        "description": job.description or "",
        "location": job.location or "",
        "location_type": str(job.location_type) if job.location_type else "",
        "employment_type": str(job.employment_type) if job.employment_type else "",
        "experience_level": str(job.experience_level) if job.experience_level else "",
        "salary_min": float(job.salary_min) if job.salary_min else None,
        "salary_max": float(job.salary_max) if job.salary_max else None,
        "salary_currency": job.salary_currency,
    }


class JobDiscoveryService(BaseService[JobRepository]):
    """Orchestrates AI-powered job matching for an authenticated user."""

    def __init__(
        self,
        job_repository: JobRepository,
        match_result_repository: MatchResultRepository,
        resume_repository: ResumeRepository,
        job_matcher: JobMatcherService | None = None,
    ) -> None:
        super().__init__(job_repository)
        self.job_repository = job_repository
        self.match_result_repository = match_result_repository
        self.resume_repository = resume_repository
        self._job_matcher = job_matcher or JobMatcherService()

    async def match_for_user(
        self,
        user_id: uuid.UUID,
        resume_id: uuid.UUID | None = None,
    ) -> JobMatchResponse:
        """Run the full match pipeline for a user.

        Args:
            user_id: The authenticated user's UUID.
            resume_id: Optional specific resume UUID. When omitted the user's
                most recently created resume is used.

        Returns:
            A :class:`JobMatchResponse` with ranked matches and AI guidance.

        Raises:
            NoResumeError: When the user has no completed resume.
            NoJobsError: When the database contains no active jobs.
            JobServiceError: When the AI matcher fails.
        """
        # Step 1 — resolve the resume.
        resume = await self._resolve_resume(user_id, resume_id)

        # Step 2 — load active jobs.
        jobs = await self.job_repository.get_active_jobs()
        if not jobs:
            raise NoJobsError("No active jobs are available for matching.")

        # Step 3 — convert to AI-friendly format.
        candidate_profile: dict[str, Any] = resume.content or {}
        available_jobs = [_job_to_dict(j) for j in jobs]

        # Step 4 — call the AI matcher.
        try:
            ai_result = await self._job_matcher.match_jobs(
                candidate_profile, available_jobs
            )
        except JobMatcherError as exc:
            raise JobServiceError(f"AI job matching failed: {exc}") from exc

        # Step 5 — build a job-id lookup for persistence.
        job_lookup: dict[str, Job] = {str(j.id): j for j in jobs}

        # Step 6 — persist match results and build response items.
        match_items: list[JobMatchItem] = []
        for raw_match in ai_result.get("top_matches", []):
            job_id_str = raw_match.get("job_id", "")
            job = job_lookup.get(job_id_str)
            if job is None:
                # AI returned a job_id that doesn't exist in our DB — skip.
                logger.warning("AI returned unknown job_id %s; skipping.", job_id_str)
                continue

            score = raw_match.get("match_score", 0)
            matching_skills = raw_match.get("matching_skills", [])
            missing_skills = raw_match.get("missing_skills", [])
            reason = raw_match.get("reason", "")

            match_record = await self.match_result_repository.create(
                user_id=user_id,
                resume_id=resume.id,
                job_id=job.id,
                match_score=Decimal(str(score)),
                matched_skills=matching_skills,
                missing_skills=missing_skills,
                strengths=[],
                weaknesses=[],
                summary=reason,
                status=_MATCH_STATUS_COMPLETED,
                matcher_type="ai",
            )

            match_items.append(
                JobMatchItem(
                    match_result_id=match_record.id,
                    job_id=job.id,
                    job_title=raw_match.get("job_title", job.title),
                    company=raw_match.get("company", job.company_name),
                    match_score=score,
                    matching_skills=matching_skills,
                    missing_skills=missing_skills,
                    reason=reason,
                )
            )

        await self.commit()

        return JobMatchResponse(
            resume_id=resume.id,
            top_matches=match_items,
            overall_match_summary=ai_result.get("overall_match_summary", ""),
            recommended_next_actions=ai_result.get("recommended_next_actions", []),
        )

    async def list_jobs(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Job], int]:
        """Return a page of active jobs and the total active count.

        Args:
            offset: Number of records to skip.
            limit: Maximum number of records to return.

        Returns:
            A tuple of (jobs list, total count).
        """
        jobs = await self.job_repository.get_active_jobs(offset=offset, limit=limit)
        total = await self.job_repository.count(is_active=True)
        return jobs, total

    async def get_job(self, job_id: uuid.UUID) -> Job | None:
        """Return a single active job by id, or None.

        Args:
            job_id: The job's UUID.

        Returns:
            The :class:`Job` instance or ``None``.
        """
        return await self.job_repository.get_by_id(job_id)

    async def create_job(self, **values: Any) -> Job:
        """Create an internal job posting and commit.

        Args:
            **values: Keyword arguments matching ``Job`` column names.

        Returns:
            The newly created :class:`Job` instance.
        """
        from backend.models.enums import JobSource

        values.setdefault("source", JobSource.INTERNAL)
        values.setdefault("is_active", True)
        job = await self.job_repository.create(**values)
        await self.commit()
        return job

    async def get_user_matches(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
        matcher_type: str | None = None,
    ) -> tuple[list[MatchResult], int]:
        """Return a user's stored match results and total count.

        Args:
            user_id: The user's UUID.
            offset: Number of records to skip.
            limit: Maximum number of records to return.
            matcher_type: Optional filter — ``deterministic`` or ``ai``.

        Returns:
            A tuple of (match results list, total count).
        """
        matches = await self.match_result_repository.get_user_matches(
            user_id, offset=offset, limit=limit, matcher_type=matcher_type
        )
        count_filters: dict[str, Any] = {"user_id": user_id}
        if matcher_type is not None:
            count_filters["matcher_type"] = matcher_type
        total = await self.match_result_repository.count(**count_filters)
        return matches, total

    # ------------------------------------------------------------------ #
    # Deterministic matching (no LLM)                                    #
    # ------------------------------------------------------------------ #

    async def deterministic_match_for_user(
        self,
        user_id: uuid.UUID,
        resume_id: uuid.UUID | None = None,
        top_n: int = 10,
    ) -> "DeterministicMatchResponse":
        """Run the deterministic matcher against all active jobs.

        Uses ``backend.matching.matcher.match_candidate_to_job`` — no LLM,
        no external API.  Results are ranked by match_score descending and
        the top ``top_n`` are returned.  MatchResult records are persisted
        for the returned matches.

        Raises:
            NoResumeError: When the user has no parsed resume.
            NoJobsError: When there are no active jobs.
        """
        from decimal import Decimal as _Dec
        from backend.matching.matcher import match_candidate_to_job
        from backend.schemas.job import DeterministicMatchItem, DeterministicMatchResponse

        resume = await self._resolve_resume(user_id, resume_id)
        candidate_profile = resume.content or {}

        jobs = await self.job_repository.get_active_jobs(limit=500)
        if not jobs:
            raise NoJobsError("No active jobs are available for matching.")

        # Score every job
        scored: list[tuple[Job, Any]] = []
        for job in jobs:
            result = match_candidate_to_job(candidate_profile, job)
            scored.append((job, result))

        # Sort descending by match_score
        scored.sort(key=lambda x: x[1].match_score, reverse=True)
        top = scored[:top_n]

        # Persist and build response items
        items: list[DeterministicMatchItem] = []
        for job, mr in top:
            await self.match_result_repository.create(
                user_id=user_id,
                resume_id=resume.id,
                job_id=job.id,
                match_score=_Dec(str(mr.match_score)),
                matched_skills=mr.matched_skills,
                missing_skills=mr.missing_skills,
                strengths=[],
                weaknesses=[],
                summary=f"{mr.match_level} — {mr.recommendation}",
                status="completed",
                matcher_type="deterministic",
            )
            items.append(DeterministicMatchItem(
                job_id=job.id,
                job_title=job.title,
                company=job.company_name,
                location=job.location,
                employment_type=str(job.employment_type) if job.employment_type else None,
                match_score=mr.match_score,
                match_level=mr.match_level,
                matched_skills=mr.matched_skills,
                missing_skills=mr.missing_skills,
                experience_match=mr.experience_match,
                role_match=mr.role_match,
                recommendation=mr.recommendation,
                skill_score=mr.skill_score,
                experience_score=mr.experience_score,
                role_score=mr.role_score,
            ))

        await self.commit()

        return DeterministicMatchResponse(
            resume_id=resume.id,
            total_jobs_evaluated=len(jobs),
            matches=items,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _resolve_resume(
        self, user_id: uuid.UUID, resume_id: uuid.UUID | None
    ) -> Resume:
        """Fetch the target resume or raise NoResumeError."""
        if resume_id is not None:
            resume = await self.resume_repository.get(resume_id)
            if resume is None or resume.user_id != user_id:
                raise NoResumeError("Specified resume not found.")
        else:
            resume = await self.resume_repository.get_latest_for_user(user_id)

        if resume is None:
            raise NoResumeError(
                "No resume found. Upload and parse a resume before requesting job matches."
            )
        if not resume.content:
            raise NoResumeError(
                "Resume has no parsed content. Ensure the resume has been successfully analyzed."
            )
        return resume


__all__ = [
    "JobDiscoveryService",
    "JobServiceError",
    "NoResumeError",
    "NoJobsError",
]
