"""Alembic environment for Jobyn AI.

Runs migrations against the application's async engine. The database URL and
target metadata are read from application settings and the shared ``Base``
registry, so migrations always match the configured runtime environment.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from backend.core.config import get_settings
from backend.database.base import Base
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object, *, render_as_batch: bool) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=render_as_batch,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations(render_as_batch: bool) -> None:
    """Run migrations against the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations, render_as_batch=render_as_batch)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode, dispatching to the async engine."""
    url = config.get_main_option("sqlalchemy.url")
    asyncio.run(run_async_migrations(render_as_batch=url.startswith("sqlite")))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
