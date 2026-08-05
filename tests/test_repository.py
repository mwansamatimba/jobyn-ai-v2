"""Tests for the generic repository foundation.

A throwaway ORM model is defined here (not in ``backend.models``) so the CRUD,
soft-delete, and pagination behavior of :class:`BaseRepository` is exercised
without shipping any feature models.
"""

import uuid

from backend.database.base import Base
from backend.models import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from backend.repositories.base import BaseRepository
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column


class Item(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "test_items"

    name: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)


async def test_create_and_get(db_session: AsyncSession) -> None:
    repo = BaseRepository[Item](db_session, Item)
    created = await repo.create(name="alice")
    await db_session.commit()

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.name == "alice"
    assert created.id is not None


async def test_get_returns_none_for_missing_row(db_session: AsyncSession) -> None:
    repo = BaseRepository[Item](db_session, Item)
    assert await repo.get(uuid.uuid4()) is None


async def test_get_by_and_list_and_count(db_session: AsyncSession) -> None:
    repo = BaseRepository[Item](db_session, Item)
    await repo.create(name="bob")
    await repo.create(name="carol")
    await db_session.commit()

    assert (await repo.get_by(name="bob")) is not None
    assert await repo.count() == 2
    assert len(await repo.list(limit=1)) == 1
    assert len(await repo.list(offset=1, limit=10)) == 1


async def test_update(db_session: AsyncSession) -> None:
    repo = BaseRepository[Item](db_session, Item)
    created = await repo.create(name="dave")
    await db_session.commit()

    updated = await repo.update(created, name="david")
    await db_session.commit()
    assert updated.name == "david"
    assert (await repo.get(created.id)).name == "david"


async def test_soft_delete_hides_row_but_keeps_it(db_session: AsyncSession) -> None:
    repo = BaseRepository[Item](db_session, Item)
    created = await repo.create(name="erin")
    await db_session.commit()

    await repo.delete(created)
    await db_session.commit()

    assert await repo.get(created.id) is None
    assert await repo.count() == 0
    assert len(await repo.list(limit=10)) == 0


async def test_hard_delete_removes_row(db_session: AsyncSession) -> None:
    repo = BaseRepository[Item](db_session, Item)
    created = await repo.create(name="frank")
    await db_session.commit()

    await repo.hard_delete(created)
    await db_session.commit()

    assert await repo.get(created.id) is None
    assert await repo.count() == 0
