"""Database bootstrap: the single source of truth for ``Base.metadata``.

Alembic's ``env.py`` and any code that needs the full table set import
``Base`` from here. Importing this module registers every model defined in the
``backend.models`` package on the shared registry, which is what lets Alembic
autogenerate migrations that cover the entire schema.
"""

from backend.database.base_class import Base
from backend.models import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin  # noqa: F401

__all__ = ["Base"]
