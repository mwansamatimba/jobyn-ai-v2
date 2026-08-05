"""Redis client factory.

Returns a lazily-connecting async Redis client. No network I/O happens at
import time, so the API can start even when Redis is unavailable; commands fail
only when actually issued.
"""

from functools import lru_cache

from backend.core.config import get_settings
from redis.asyncio import Redis


@lru_cache
def get_redis() -> Redis:
    """Return a cached async Redis client bound to the configured URL."""
    settings = get_settings()
    return Redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
