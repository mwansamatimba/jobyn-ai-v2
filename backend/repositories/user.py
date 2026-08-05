"""Data access for the :class:`User` model."""

from backend.models.user import User
from backend.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """User-specific queries layered on the generic async repository."""

    async def get_by_email(self, email: str) -> User | None:
        """Fetch an active (non-soft-deleted) user by normalized email."""
        return await self.get_by(email=email)
