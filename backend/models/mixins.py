"""Reusable ORM column mixins.

Mixin column types come from the annotation map on the shared ``Base``
(see ``backend/database/base_class.py``), so ``id`` is a portable UUID and
timestamps are timezone-aware ``DateTime`` on every supported backend.
"""

import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    """Adds a portable UUID primary key column named ``id``."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Adds a nullable ``deleted_at`` column used for soft deletion.

    Rows are never physically removed through the standard repository
    ``delete`` operation; the repository sets ``deleted_at`` instead and
    automatically excludes soft-deleted rows from reads. Use ``hard_delete``
    only when physical removal is genuinely required.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
