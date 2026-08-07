"""Data access for resume-domain models."""

from __future__ import annotations

import uuid

from backend.models.resume import Resume, UploadedResume
from backend.repositories.base import BaseRepository


class UploadedResumeRepository(BaseRepository[UploadedResume]):
    """CRUD for :class:`UploadedResume` records."""

    async def get_by_user(self, user_id: uuid.UUID) -> list[UploadedResume]:
        """Return all upload records owned by a user."""
        return list(await self.list(user_id=user_id))


class ResumeRepository(BaseRepository[Resume]):
    """CRUD for canonical :class:`Resume` records."""

    async def get_latest_for_user(self, user_id: uuid.UUID) -> Resume | None:
        """Return the most recently created resume for a user, or None."""
        from sqlalchemy import select

        stmt = (
            self._apply_soft_delete_filter(
                select(Resume).where(
                    Resume.user_id == user_id,
                    Resume.deleted_at.is_(None),
                )
            )
            .order_by(Resume.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
