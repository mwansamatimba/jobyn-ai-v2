"""Test configuration."""

import os
import tempfile
from collections.abc import AsyncIterator

_TEST_DIR = tempfile.mkdtemp(prefix="jobyn_test_")

# ---------------------------------------------------------------------------
# Force tests to use an isolated SQLite database.
# ---------------------------------------------------------------------------

os.environ["ENVIRONMENT"] = "test"

os.environ["DATABASE_URL"] = (
    f"sqlite+aiosqlite:///{_TEST_DIR}/test.db"
)

os.environ["SECRET_KEY"] = (
    "test-secret-key-0123456789abcdef0123456789abcdef"
)

import asyncio  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from backend.database.base import Base  # noqa: E402
from backend.database.session import async_session_factory, engine  # noqa: E402
from backend.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402


def _create_schema() -> None:
    """Create all registered tables on the test engine."""

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_run())


@pytest.fixture(scope="session")
def client() -> TestClient:
    """A TestClient backed by the shared test database."""
    _create_schema()
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a freshly migrated test schema."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        yield session
