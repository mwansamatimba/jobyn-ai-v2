"""Single SQLAlchemy declarative registry.

There is exactly one ``DeclarativeBase`` subclass in the whole application.
Every ORM model inherits from it. Because all models share this registry, the
SQLAlchemy mapper can never see conflicting table or class configurations, which
is the root cause of most ``MapperConfigurationError`` failures.

Python-side type hints are mapped to portable SQLAlchemy column types through
``type_annotation_map``:

* ``uuid.UUID``      -> :class:`~sqlalchemy.Uuid` (native ``UUID`` on PostgreSQL,
  ``CHAR(32)`` on SQLite)
* ``datetime``       -> ``DateTime(timezone=True)``
* ``date``           -> ``Date``
* ``Decimal``        -> ``Numeric(12, 2)``
* ``float``/``int``/``bool``/``str`` -> ``Float``/``Integer``/``Boolean``/``String``
* ``dict``/``list``  -> ``JSON``

Providing a ``type_annotation_map`` on a custom base replaces the SQLAlchemy
defaults, so the standard Python types are listed explicitly. Explicit column
types passed to :func:`mapped_column` always take precedence over this map.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, Numeric, String, Uuid
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Application-wide declarative base for all ORM models."""

    type_annotation_map: dict[type, object] = {  # noqa: RUF012
        uuid.UUID: Uuid,
        datetime: DateTime(timezone=True),
        date: Date,
        Decimal: Numeric(12, 2),
        float: Float,
        int: Integer,
        bool: Boolean,
        str: String,
        dict: JSON,
        list: JSON,
    }
