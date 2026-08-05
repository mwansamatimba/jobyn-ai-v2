"""Async engine and session factory.

The engine is created once at import time from the current settings. It is the
only place that decides between PostgreSQL and the SQLite fallback based on the
``DATABASE_URL`` scheme, so repositories and services never touch engine
configuration.

Transactions are not committed here. Repositories call :meth:`AsyncSession.flush`
so the unit-of-work boundary (a service method or an endpoint) decides when to
commit via ``await session.commit()`` or ``await session.rollback()``.
"""

from collections.abc import Mapping
from typing import Any

from backend.core.config import get_settings
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

_settings = get_settings()


def _engine_options(database_url: str) -> Mapping[str, Any]:
    if database_url.startswith("sqlite"):
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 1800,
    }


engine: AsyncEngine = create_async_engine(
    _settings.DATABASE_URL,
    echo=_settings.DEBUG,
    future=True,
    **_engine_options(_settings.DATABASE_URL),
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
