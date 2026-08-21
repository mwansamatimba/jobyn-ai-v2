"""Data access for career insight domain models.

Follows the same patterns as ``backend/repositories/job.py`` and
``backend/repositories/resume.py``: repositories only flush, never commit;
all queries go through ``BaseRepository`` helpers or explicit SQLAlchemy
``select`` statements.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.models.career import CareerInsight
from backend.repositories.base import BaseRepository


class CareerInsightRepository(BaseRepository[CareerInsight]):
    """CRUD for :class:`CareerInsight` records."""

    async def save(self, **values) -> CareerInsight:
        """Insert a new career insight record and flush (no commit).

        Args:
            **values: Column values for the new row.

        Returns:
            The newly created :class:`CareerInsight` instance.
        """
        return await self.create(**values)

    async def get_latest_for_user(self, user_id: uuid.UUID) -> CareerInsight | None:
        """Return the most recently created career insight for a user, or None.

        Args:
            user_id: The user's UUID.

        Returns:
            The latest :class:`CareerInsight` or ``None``.
        """
        stmt = (
            select(CareerInsight)
            .where(CareerInsight.user_id == user_id)
            .order_by(CareerInsight.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> list[CareerInsight]:
        """Return a page of career insights for a user, newest first.

        Args:
            user_id: The user's UUID.
            offset: Number of records to skip.
            limit: Maximum number of records to return.

        Returns:
            A list of :class:`CareerInsight` instances.
        """
        stmt = (
            select(CareerInsight)
            .where(CareerInsight.user_id == user_id)
            .order_by(CareerInsight.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """Return the total number of insights for a user.

        Args:
            user_id: The user's UUID.

        Returns:
            Row count as an integer.
        """
        return await self.count(user_id=user_id)
