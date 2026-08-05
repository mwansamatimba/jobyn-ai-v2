"""Version 1 API router aggregation.

New endpoint modules are included here; the router is then mounted once in
``app/api/router.py`` under the global prefix. This gives every feature a
single registration point and keeps the URL tree predictable.
"""

from fastapi import APIRouter

from backend.api.v1.endpoints import auth, health

router = APIRouter()
router.include_router(auth.router)
router.include_router(health.router)
