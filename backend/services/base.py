"""Generic async service base class.

Feature services subclass :class:`BaseService` and add business logic that
combines repository calls. Keeping every service wired to exactly one
repository prevents duplicated logic and keeps dependencies explicit (services
receive their repository via constructor injection, which makes them trivial to
unit-test with fakes).
"""

from typing import Any

from backend.repositories.base import BaseRepository


class BaseService[RepositoryType: BaseRepository[Any]]:
    """Holds a single repository and exposes shared transaction helpers."""

    def __init__(self, repository: RepositoryType) -> None:
        self.repository = repository

    async def commit(self) -> None:
        """Commit the current unit of work."""
        await self.repository.session.commit()

    async def rollback(self) -> None:
        """Roll back the current unit of work."""
        await self.repository.session.rollback()
