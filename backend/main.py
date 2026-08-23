"""FastAPI application entry point for Jobyn AI."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from backend.api.router import api_router
from backend.core.errors import register_exception_handlers


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DEMO_HTML = Path(__file__).parent / "demo.html"


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialize application resources on startup.

    For SQLite development/demo environments, automatically create all
    database tables if they do not already exist.

    PostgreSQL schema management remains the responsibility of Alembic.
    """

    from backend.core.config import get_settings

    settings = get_settings()

    if settings.DATABASE_URL.startswith("sqlite"):
        # Import Base so that all SQLAlchemy models are registered
        # before create_all() is called.
        from backend.database.base import Base
        from backend.database.session import engine

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    yield


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Jobyn AI",
        description="AI-powered career intelligence platform",
        version="2.0.0",
        lifespan=_lifespan,
    )

    # -----------------------------------------------------------------------
    # Exception handlers
    # -----------------------------------------------------------------------

    register_exception_handlers(app)

    # -----------------------------------------------------------------------
    # API router
    # -----------------------------------------------------------------------

    app.include_router(api_router)

    # -----------------------------------------------------------------------
    # System routes
    # -----------------------------------------------------------------------

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Return basic service information."""

        return {
            "service": "Jobyn AI",
            "status": "running",
        }

    # -----------------------------------------------------------------------
    # Demo interface
    # -----------------------------------------------------------------------

    @app.get(
        "/demo",
        tags=["demo"],
        include_in_schema=False,
    )
    async def demo():
        """Serve the single-page MVP demo interface."""

        return FileResponse(
            _DEMO_HTML,
            media_type="text/html",
        )

    # -----------------------------------------------------------------------
    # Health checks
    # -----------------------------------------------------------------------

    @app.get(
        "/api/v1/health",
        tags=["health"],
    )
    async def health_check() -> dict[str, str]:
        """Basic application health check."""

        return {
            "status": "ok",
            "service": "Jobyn AI",
        }

    @app.get(
        "/api/v1/health/ready",
        tags=["health"],
    )
    async def readiness_check() -> dict[str, str]:
        """Application readiness check."""

        return {
            "status": "ready",
        }

    # -----------------------------------------------------------------------
    # LLM health check
    # -----------------------------------------------------------------------

    @app.get(
        "/api/v1/health/llm",
        tags=["health"],
    )
    async def llm_health() -> dict[str, Any]:
        """Return the configured LLM provider and model."""

        from backend.ai.llm import NVIDIA_API_KEY, NVIDIA_MODEL

        return {
            "llm_configured": bool(NVIDIA_API_KEY),
            "provider": "nvidia_nim",
            "model": NVIDIA_MODEL,
        }

    return app


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = create_app()