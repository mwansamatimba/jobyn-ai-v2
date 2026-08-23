"""Regression test: application startup creates the users table.

Verifies that when the FastAPI app starts against a fresh SQLite database,
the lifespan handler runs create_all() so the users table (and all other
tables) are present before the first request.

This test reuses the session-scoped ``client`` fixture from conftest.py,
which itself calls create_all() — the fixture and the lifespan handler are
both idempotent, so running both is safe and correctly simulates startup.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_users_table_exists_after_startup(db_session: AsyncSession):
    """The users table must exist in the SQLite database after startup."""
    result = await db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    )
    row = result.scalar_one_or_none()
    assert row == "users", (
        "users table not found — lifespan create_all() did not run or "
        "models were not registered before create_all()."
    )


@pytest.mark.asyncio
async def test_all_core_tables_exist_after_startup(db_session: AsyncSession):
    """All core domain tables must exist after startup."""
    expected = {
        "users", "resumes", "jobs", "applications",
        "match_results", "career_insights", "uploaded_resumes",
    }
    result = await db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    existing = {row[0] for row in result.fetchall()}
    missing = expected - existing
    assert not missing, f"Tables missing after startup: {missing}"
