"""Async database engine and session factory.

This module is the single source of truth for SQLAlchemy engine and session
configuration.

Responsibilities:
- Create the SQLAlchemy AsyncEngine once at import time.
- Automatically use the correct async SQLite driver (aiosqlite).
- Support PostgreSQL with an async driver.
- Configure SQLite appropriately for local development/testing.
- Provide an async_sessionmaker for request-scoped sessions.
- Provide get_session() for FastAPI dependency injection.

Transaction management intentionally does NOT happen here.

Repositories/services are responsible for:
    await session.flush()

and the surrounding unit of work is responsible for:
    await session.commit()
    await session.rollback()
"""

from collections.abc import AsyncGenerator, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from backend.core.config import get_settings


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_settings = get_settings()


# ---------------------------------------------------------------------------
# Database URL normalization
# ---------------------------------------------------------------------------

def _normalize_database_url(database_url: str) -> str:
    """Convert configured database URLs to async SQLAlchemy URLs.

    SQLite:
        sqlite:///./jobyn.db
            ->
        sqlite+aiosqlite:///./jobyn.db

    PostgreSQL:
        postgresql://...
            ->
        postgresql+asyncpg://...

    Already-correct async URLs are returned unchanged.

    Args:
        database_url: Database URL from application settings.

    Returns:
        A database URL compatible with SQLAlchemy's async engine.
    """
    database_url = database_url.strip()

    # SQLite ---------------------------------------------------------------
    if database_url.startswith("sqlite://"):
        if database_url.startswith("sqlite+aiosqlite://"):
            return database_url

        return database_url.replace(
            "sqlite://",
            "sqlite+aiosqlite://",
            1,
        )

    # PostgreSQL -----------------------------------------------------------
    if database_url.startswith("postgresql://"):
        return database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    if database_url.startswith("postgres://"):
        return database_url.replace(
            "postgres://",
            "postgresql+asyncpg://",
            1,
        )

    # Already using an async PostgreSQL driver.
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url

    # Any other SQLAlchemy-compatible async URL is passed through.
    return database_url


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------

def _engine_options(database_url: str) -> Mapping[str, Any]:
    """Return engine options based on the database backend.

    SQLite is optimized for local development and testing.

    PostgreSQL uses connection pooling and health checks.
    """
    if database_url.startswith("sqlite+aiosqlite://"):
        return {
            "connect_args": {
                "check_same_thread": False,
            },
            "poolclass": StaticPool,
        }

    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 1800,
    }


# ---------------------------------------------------------------------------
# Database URL
# ---------------------------------------------------------------------------

DATABASE_URL = _normalize_database_url(
    _settings.DATABASE_URL
)


# ---------------------------------------------------------------------------
# Async SQLAlchemy engine
# ---------------------------------------------------------------------------

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=_settings.DEBUG,
    **_engine_options(DATABASE_URL),
)


# ---------------------------------------------------------------------------
# Async session factory
# ---------------------------------------------------------------------------

async_session_factory: async_sessionmaker[AsyncSession] = (
    async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
)


# ---------------------------------------------------------------------------
# Backwards-compatible alias
# ---------------------------------------------------------------------------

# Used by backend.api.deps.py and potentially other modules.
async_session_maker = async_session_factory


# ---------------------------------------------------------------------------
# FastAPI database dependency
# ---------------------------------------------------------------------------

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped asynchronous database session.

    The session lifecycle is managed here.

    Transactions are intentionally NOT committed or rolled back here.
    Services/endpoints control the unit-of-work boundary.

    Yields:
        An open AsyncSession for the current request.
    """
    async with async_session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Engine shutdown
# ---------------------------------------------------------------------------

async def dispose_engine() -> None:
    """Dispose of the SQLAlchemy connection pool.

    This can be called during FastAPI application shutdown.
    """
    await engine.dispose()