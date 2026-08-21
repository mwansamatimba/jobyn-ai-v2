"""Data access for the Application model.

Follows the same conventions as every other repository in this project:
- Only flushes — never commits.
- All queries through BaseRepository helpers or explicit selects.
- No business logic — that lives in the service layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.models.enums import ApplicationStatus
from backend.models.job import Application
from backend.repositories.base import BaseRepository


class ApplicationRepository(BaseRepository[Application]):
    """CRUD for :class:`Application` records."""

    async def get_by_id(self, application_id: uuid.UUID) -> Application | None:
        """Fetch an application by primary key (no soft-delete on this model)."""
        return await self.get(application_id)

    async def get_user_application(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
    ) -> Application | None:
        """Fetch a specific application belonging to a user, or None.

        Ownership is enforced here — the query filters by both primary key and
        user_id so a mismatched user_id simply returns None.
        """
        stmt = select(Application).where(
            Application.id == application_id,
            Application.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_existing_application(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> Application | None:
        """Return the application for a (user, job) pair if it exists.

        Used to enforce the unique constraint before attempting an insert.
        """
        stmt = select(Application).where(
            Application.user_id == user_id,
            Application.job_id == job_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
        status: ApplicationStatus | None = None,
        job_id: uuid.UUID | None = None,
    ) -> list[Application]:
        """Return a filtered, paginated list of applications for a user.

        Args:
            user_id: The owning user's UUID.
            offset: Number of records to skip.
            limit: Maximum records to return.
            status: Optional status filter.
            job_id: Optional job filter.

        Returns:
            A list of :class:`Application` instances, newest first.
        """
        stmt = (
            select(Application)
            .where(Application.user_id == user_id)
        )
        if status is not None:
            stmt = stmt.where(Application.status == status)
        if job_id is not None:
            stmt = stmt.where(Application.job_id == job_id)
        stmt = stmt.order_by(Application.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(
        self,
        user_id: uuid.UUID,
        *,
        status: ApplicationStatus | None = None,
        job_id: uuid.UUID | None = None,
    ) -> int:
        """Count applications for a user with optional filters."""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(Application).where(
            Application.user_id == user_id
        )
        if status is not None:
            stmt = stmt.where(Application.status == status)
        if job_id is not None:
            stmt = stmt.where(Application.job_id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()
