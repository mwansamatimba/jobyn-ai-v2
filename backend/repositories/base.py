"""Generic async repository base class.

Feature repositories subclass :class:`BaseRepository` and only add
domain-specific query methods. The generic CRUD operations below are shared by
every feature, which eliminates duplicated data-access logic.

Repositories operate on a single :class:`AsyncSession` injected at construction
time and use ``flush`` (never ``commit``) so the surrounding unit of work keeps
control of the transaction boundary.

Soft deletion is transparent: when the model includes the
:class:`~backend.models.mixins.SoftDeleteMixin`, reads automatically exclude
soft-deleted rows, :meth:`delete` sets ``deleted_at`` instead of removing the
row, and :meth:`hard_delete` performs the physical removal.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.base_class import Base


class BaseRepository[ModelType: Base]:
    """Thin data-access layer for a single ORM model."""

    def __init__(self, session: AsyncSession, model: type[ModelType]) -> None:
        self.session = session
        self.model = model

    def _supports_soft_delete(self) -> bool:
        return hasattr(self.model, "deleted_at")

    async def get(self, id_value: uuid.UUID) -> ModelType | None:
        """Fetch a single row by primary key, returning ``None`` when absent.

        Soft-deleted rows are treated as absent.
        """
        instance = await self.session.get(self.model, id_value)
        if (
            instance is not None
            and self._supports_soft_delete()
            and instance.deleted_at is not None  # type: ignore[attr-defined]
        ):
            return None
        return instance

    async def get_by(self, **filters: Any) -> ModelType | None:
        """Fetch a single row matching the given column filters."""
        stmt = self._apply_soft_delete_filter(select(self.model).filter_by(**filters))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        order_by: Any = None,
        **filters: Any,
    ) -> Sequence[ModelType]:
        """Fetch a page of rows, optionally filtered and ordered."""
        stmt = self._apply_soft_delete_filter(select(self.model))
        if filters:
            stmt = stmt.filter_by(**filters)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, **filters: Any) -> int:
        """Count rows matching the given column filters."""
        stmt = self._apply_soft_delete_filter(select(func.count()).select_from(self.model))
        if filters:
            stmt = stmt.filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def create(self, **values: Any) -> ModelType:
        """Insert a new row from keyword values."""
        instance = self.model(**values)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def update(self, instance: ModelType, **values: Any) -> ModelType:
        """Apply the given attribute changes to an existing instance."""
        for key, value in values.items():
            setattr(instance, key, value)
        await self.session.flush()
        return instance

    async def delete(self, instance: ModelType) -> None:
        """Delete a row.

        For models with soft delete, ``deleted_at`` is stamped instead of
        physically removing the row. For all other models the row is removed.
        """
        if self._supports_soft_delete():
            instance.deleted_at = datetime.now(UTC)  # type: ignore[attr-defined]
        else:
            await self.session.delete(instance)
        await self.session.flush()

    async def hard_delete(self, instance: ModelType) -> None:
        """Physically remove a row regardless of the soft-delete mixin."""
        await self.session.delete(instance)
        await self.session.flush()

    def _apply_soft_delete_filter(self, stmt: Any) -> Any:
        if self._supports_soft_delete():
            return stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt
