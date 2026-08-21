"""Interview preparation service.

Thin orchestration layer — no AI logic lives here.
Pipeline:
  1. Load the application (ownership enforced via repository).
  2. Load the associated Job to get the target role (title).
  3. Load the user's latest Resume and extract Resume.content.
  4. Call the existing InterviewCoachService.generate_interview_plan().
  5. Return the plan — stateless, no persistence.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.ai.interview_coach import InterviewCoachError, InterviewCoachService
from backend.repositories.application import ApplicationRepository
from backend.repositories.job import JobRepository
from backend.repositories.resume import ResumeRepository
from backend.services.base import BaseService


class InterviewServiceError(Exception):
    """Raised when interview preparation cannot be generated."""


class ApplicationNotFoundError(InterviewServiceError):
    """Application missing or does not belong to the user."""


class JobNotFoundError(InterviewServiceError):
    """Job referenced by the application no longer exists."""


class NoResumeError(InterviewServiceError):
    """User has no parsed resume to use as the candidate profile."""


class InterviewPreparationService(BaseService[ApplicationRepository]):
    """Orchestrates interview prep generation for an authenticated user."""

    def __init__(
        self,
        application_repository: ApplicationRepository,
        job_repository: JobRepository,
        resume_repository: ResumeRepository,
        coach: InterviewCoachService | None = None,
    ) -> None:
        super().__init__(application_repository)
        self.application_repository = application_repository
        self.job_repository = job_repository
        self.resume_repository = resume_repository
        self._coach = coach or InterviewCoachService()

    async def generate_for_application(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Generate an interview preparation plan for a user's application.

        Args:
            user_id: JWT-derived user UUID.
            application_id: UUID of the application to prepare for.

        Returns:
            The dict produced by
            :meth:`~backend.ai.interview_coach.InterviewCoachService.generate_interview_plan`,
            augmented with ``application_id`` and ``job_id``.

        Raises:
            ApplicationNotFoundError: Application missing or belongs to another user.
            JobNotFoundError: The job linked to the application no longer exists.
            NoResumeError: User has no parsed resume.
            InterviewServiceError: AI generation failed.
        """
        # Step 1 — load application (ownership enforced).
        application = await self.application_repository.get_user_application(
            user_id, application_id
        )
        if application is None:
            raise ApplicationNotFoundError(
                f"Application {application_id} not found."
            )

        # Step 2 — load the job to get the target role title.
        job = await self.job_repository.get_by_id(application.job_id)
        if job is None:
            raise JobNotFoundError(
                f"Job associated with application {application_id} no longer exists."
            )

        # Step 3 — load the user's own resume.
        resume = await self.resume_repository.get_latest_for_user(user_id)
        if resume is None or not resume.content:
            raise NoResumeError(
                "No parsed resume found. Upload and parse a resume before "
                "requesting interview preparation."
            )

        # Step 4 — call the existing AI service.
        target_role = job.title
        candidate_profile: dict[str, Any] = dict(resume.content)

        try:
            plan = await self._coach.generate_interview_plan(
                candidate_profile, target_role
            )
        except InterviewCoachError as exc:
            raise InterviewServiceError(
                f"Interview preparation generation failed: {exc}"
            ) from exc

        # Step 5 — enrich with IDs and return.
        plan["application_id"] = application_id
        plan["job_id"] = application.job_id
        return plan


__all__ = [
    "InterviewPreparationService",
    "InterviewServiceError",
    "ApplicationNotFoundError",
    "JobNotFoundError",
    "NoResumeError",
]
