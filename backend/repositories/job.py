"""Data access for job-domain models.

Follows the exact same patterns as ``backend/repositories/resume.py`` and
``backend/repositories/user.py``: all queries go through ``BaseRepository``
helpers; repositories only flush, never commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.models.job import Job, MatchResult
from backend.repositories.base import BaseRepository


class JobRepository(BaseRepository[Job]):
    """CRUD for :class:`Job` records."""

    async def get_active_jobs(self, *, offset: int = 0, limit: int = 100) -> list[Job]:
        """Return a page of active, non-soft-deleted jobs ordered by newest first."""
        stmt = (
            self._apply_soft_delete_filter(
                select(Job).where(Job.is_active.is_(True))
            )
            .order_by(Job.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        """Fetch a non-soft-deleted job by primary key."""
        return await self.get(job_id)

    async def get_by_external_url(self, external_url: str) -> Job | None:
        """Return a job matching the given external URL, or None.

        Used for deduplication during ingestion — if a job with this URL
        already exists (active or soft-deleted), skip it.
        """
        from sqlalchemy import select as _select

        stmt = _select(Job).where(Job.external_url == external_url).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, **values) -> Job:  # type: ignore[override]
        """Insert a new job record."""
        return await super().create(**values)


class MatchResultRepository(BaseRepository[MatchResult]):
    """CRUD for :class:`MatchResult` records."""

    async def create(self, **values) -> MatchResult:  # type: ignore[override]
        """Insert a new match result record."""
        return await super().create(**values)

    async def get_user_matches(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[MatchResult]:
        """Return match results for a user, newest first."""
        stmt = (
            select(MatchResult)
            .where(MatchResult.user_id == user_id)
            .order_by(MatchResult.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_match_results(
        self,
        user_id: uuid.UUID,
        resume_id: uuid.UUID,
    ) -> list[MatchResult]:
        """Return all match results for a specific user+resume pair, newest first."""
        stmt = (
            select(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.resume_id == resume_id,
            )
            .order_by(MatchResult.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
