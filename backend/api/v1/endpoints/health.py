"""Health and readiness endpoints for liveness probes.

``/health`` performs no I/O and is safe to hit on every Cloud Run request.
``/health/ready`` verifies database connectivity, which is the signal startup
probes should depend on.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe: returns 200 when the process is serving."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness probe: verifies the database is reachable."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready"}
