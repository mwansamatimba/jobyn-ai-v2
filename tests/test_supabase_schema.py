import pytest
from sqlalchemy import text

from backend.database.session import engine


@pytest.mark.asyncio
async def test_supabase_database_identity():
    if engine.url.get_backend_name() != "postgresql":
        pytest.skip("Supabase database identity test requires PostgreSQL")

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT
                    current_database(),
                    current_user,
                    version()
                """
            )
        )

        database, user, version = result.one()

        assert database
        assert user
        assert version