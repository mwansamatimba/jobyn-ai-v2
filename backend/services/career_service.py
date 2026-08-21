"""Career navigator and skill gap analysis service.

Owns the full career analysis pipeline:
  1. Load the user's latest Resume from the database.
  2. Extract Resume.content as the candidate profile.
  3. Call the existing stateless CareerNavigatorService (AI layer).
  4. Persist the structured result as a CareerInsight record.
  5. Return the persisted insight.

All transaction boundaries are managed here. Routes stay free of business
logic and session lifecycle concerns, following the same pattern as
ResumeService and JobDiscoveryService.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.ai.career_navigator import CareerNavigatorError, CareerNavigatorService
from backend.models.career import CareerInsight
from backend.models.resume import Resume
from backend.repositories.career import CareerInsightRepository
from backend.repositories.resume import ResumeRepository
from backend.services.base import BaseService

logger = logging.getLogger(__name__)


class CareerServiceError(Exception):
    """Raised when the career analysis pipeline fails."""


class NoResumeError(CareerServiceError):
    """Raised when the user has no parsed resume to analyse."""


class CareerNavigatorService:
    """Thin wrapper — deliberately re-exported so routes only import from here."""


class CareerAnalysisService(BaseService[CareerInsightRepository]):
    """Orchestrates AI-powered career analysis for an authenticated user."""

    def __init__(
        self,
        insight_repository: CareerInsightRepository,
        resume_repository: ResumeRepository,
        navigator: CareerNavigatorService | None = None,
    ) -> None:
        super().__init__(insight_repository)
        self.insight_repository = insight_repository
        self.resume_repository = resume_repository
        # Use the real AI navigator from backend/ai/career_navigator.py
        from backend.ai.career_navigator import (
            CareerNavigatorService as _RealNavigator,
        )
        self._navigator: _RealNavigator = navigator or _RealNavigator()

    async def analyse_for_user(
        self,
        user_id: uuid.UUID,
        target_role: str | None = None,
    ) -> CareerInsight:
        """Run the full career analysis pipeline for a user.

        Args:
            user_id: The authenticated user's UUID.
            target_role: Optional role the user wants guidance towards.
                Injected into the candidate profile before calling the AI so
                the navigator can tailor its recommendations.

        Returns:
            The persisted :class:`CareerInsight` record.

        Raises:
            NoResumeError: When the user has no parsed resume.
            CareerServiceError: When the AI navigator fails.
        """
        # Step 1 — load latest resume.
        resume = await self.resume_repository.get_latest_for_user(user_id)
        if resume is None or not resume.content:
            raise NoResumeError(
                "No parsed resume found. Upload and parse a resume before "
                "requesting a career analysis."
            )

        # Step 2 — build the profile dict, optionally augmented with target role.
        profile: dict[str, Any] = dict(resume.content)
        if target_role:
            profile["target_role"] = target_role

        # Step 3 — call the AI layer.
        try:
            analysis = await self._navigator.navigate(profile)
        except CareerNavigatorError as exc:
            raise CareerServiceError(f"AI career analysis failed: {exc}") from exc

        # Step 4 — persist the result.
        insight = await self.insight_repository.save(
            user_id=user_id,
            resume_id=resume.id,
            target_role=target_role,
            analysis=analysis,
        )
        await self.commit()

        return insight

    async def get_latest_insight(self, user_id: uuid.UUID) -> CareerInsight | None:
        """Return the most recent career insight for a user, or None.

        Args:
            user_id: The user's UUID.

        Returns:
            The latest :class:`CareerInsight` or ``None``.
        """
        return await self.insight_repository.get_latest_for_user(user_id)

    async def list_insights(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[CareerInsight], int]:
        """Return a paginated list of career insights for a user.

        Args:
            user_id: The user's UUID.
            offset: Number of records to skip.
            limit: Maximum number of records to return.

        Returns:
            A tuple of (insights list, total count).
        """
        insights = await self.insight_repository.list_for_user(
            user_id, offset=offset, limit=limit
        )
        total = await self.insight_repository.count_for_user(user_id)
        return insights, total


__all__ = [
    "CareerAnalysisService",
    "CareerServiceError",
    "NoResumeError",
]
