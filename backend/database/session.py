"""Database engine and session management for Jobyn AI."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import get_settings


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

settings = get_settings()


def _normalize_async_database_url(url: str) -> str:
    """Normalize PostgreSQL URLs to the asyncpg SQLAlchemy dialect.

    SQLAlchemy's PostgreSQL dialect defaults to psycopg2 when no async driver
    is named. Jobyn's application uses AsyncEngine throughout, so PostgreSQL
    URLs must resolve to asyncpg. This also makes the deployment resilient to
    hosting providers that expose a standard ``postgresql://`` connection URL.
    SQLite URLs are left unchanged for tests and local development.
    """
    replacements = (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql+psycopg://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("postgres://", "postgresql+asyncpg://"),
    )

    for prefix, async_prefix in replacements:
        if url.startswith(prefix):
            return async_prefix + url[len(prefix) :]

    return url


DATABASE_URL = _normalize_async_database_url(settings.DATABASE_URL)


# ---------------------------------------------------------------------------
# Database Engine Configuration
# ---------------------------------------------------------------------------
#
# Jobyn AI supports:
#
#   Production:
#       PostgreSQL + asyncpg
#
#   Tests:
#       SQLite + aiosqlite
#
# Supabase's transaction pooler uses PgBouncer. With asyncpg, prepared
# statement caching should be disabled by setting:
#
#       statement_cache_size = 0
#
# However, that argument is NOT supported by aiosqlite.
#
# Therefore connect_args must be selected based on the database URL.
# ---------------------------------------------------------------------------

connect_args: dict = {}

if DATABASE_URL.startswith("postgresql+asyncpg://"):
    connect_args = {
        "statement_cache_size": 0,
    }


# ---------------------------------------------------------------------------
# Database Engine
# ---------------------------------------------------------------------------

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)


# ---------------------------------------------------------------------------
# Async Session Factory
# ---------------------------------------------------------------------------

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Backwards-compatible aliases
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_session_maker
async_session_factory = async_session_maker


# ---------------------------------------------------------------------------
# FastAPI database dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session to FastAPI routes."""

    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
