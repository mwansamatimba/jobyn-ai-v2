"""Alembic migration environment for Jobyn AI."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.core.config import get_settings
from backend.database.base import Base


# ---------------------------------------------------------------------------
# Alembic configuration
# ---------------------------------------------------------------------------

config = context.config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ---------------------------------------------------------------------------
# Database URL
# ---------------------------------------------------------------------------
#
# Alembic uses Python ConfigParser.
#
# ConfigParser treats "%" as interpolation syntax.
#
# Supabase passwords may contain encoded characters such as:
#
#     %40
#
# Therefore "%" must become "%%" before putting the URL into Alembic's
# configuration object.
# ---------------------------------------------------------------------------

database_url = get_settings().DATABASE_URL.replace("%", "%%")

config.set_main_option(
    "sqlalchemy.url",
    database_url,
)


# ---------------------------------------------------------------------------
# SQLAlchemy metadata
# ---------------------------------------------------------------------------

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migrations
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations without establishing a database connection."""

    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
        compare_server_default=True,
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migration helper
# ---------------------------------------------------------------------------

def do_run_migrations(
    connection,
    *,
    render_as_batch: bool,
) -> None:
    """Run migrations using an active database connection."""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=render_as_batch,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Async migrations
# ---------------------------------------------------------------------------

async def run_async_migrations(
    render_as_batch: bool,
) -> None:
    """Create an async engine and run migrations."""

    connectable = async_engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,

        # -------------------------------------------------------------------
        # IMPORTANT:
        # Supabase port 6543 uses PgBouncer transaction pooling.
        # Disable asyncpg prepared statement caching.
        # -------------------------------------------------------------------
        connect_args={
            "statement_cache_size": 0,
        },
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(
                do_run_migrations,
                render_as_batch=render_as_batch,
            )
    finally:
        await connectable.dispose()


# ---------------------------------------------------------------------------
# Online migrations
# ---------------------------------------------------------------------------

def run_migrations_online() -> None:
    """Run migrations against the configured database."""

    url = config.get_main_option("sqlalchemy.url")

    asyncio.run(
        run_async_migrations(
            render_as_batch=url.startswith("sqlite"),
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()