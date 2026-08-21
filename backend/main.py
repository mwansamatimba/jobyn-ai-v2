"""FastAPI application entry point for Jobyn AI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from backend.api.router import api_router
from backend.ai.gemini import (
    GeminiConfigurationError,
    get_gemini_client,
)
from backend.core.errors import register_exception_handlers

_DEMO_HTML = Path(__file__).parent / "demo.html"


def create_app() -> FastAPI:

    app = FastAPI(
        title="Jobyn AI",
        description="AI-powered career intelligence platform",
        version="2.0.0",
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/", tags=["system"])
    async def root():
        return {
            "service": "Jobyn AI",
            "status": "running",
        }

    @app.get("/demo", tags=["demo"], include_in_schema=False)
    async def demo():
        """Serve the single-page MVP demo interface."""
        return FileResponse(_DEMO_HTML, media_type="text/html")

    @app.get("/api/v1/health", tags=["health"])
    async def health_check():
        return {
            "status": "ok",
            "service": "Jobyn AI",
        }

    @app.get("/api/v1/health/ready", tags=["health"])
    async def readiness_check():
        return {
            "status": "ready",
        }

    @app.get("/api/v1/health/gemini", tags=["health"])
    async def gemini_health() -> dict[str, Any]:
        try:
            client = get_gemini_client()
        except GeminiConfigurationError:
            return {
                "gemini_configured": False,
                "model": "",
            }

        return {
            "gemini_configured": True,
            "model": client.model,
        }

    return app



app = create_app()