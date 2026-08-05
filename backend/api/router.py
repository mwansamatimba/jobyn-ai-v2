"""Top-level API router aggregation.

Mounted on the application under ``Settings.API_V1_PREFIX``. Future API
versions are added here without touching the application factory.
"""

from fastapi import APIRouter

from backend.api.v1.router import router as v1_router

api_router = APIRouter()
api_router.include_router(v1_router)
