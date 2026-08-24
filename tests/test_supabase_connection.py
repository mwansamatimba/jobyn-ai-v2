import pytest
from sqlalchemy import text

from backend.database.session import engine


@pytest.mark.asyncio
async def test_supabase_database_connection():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
