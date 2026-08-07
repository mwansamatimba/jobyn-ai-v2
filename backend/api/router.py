"""Aggregate API router for Jobyn AI."""

from fastapi import APIRouter

from backend.api.routes import auth
from backend.api.routes import prototype
from backend.api.routes import resume
from backend.api.routes import users

api_router = APIRouter(prefix="/api/v1")

# Authentication endpoints
api_router.include_router(auth.router)

# User profile endpoints
api_router.include_router(users.router)

# Resume upload and candidate profile endpoints
api_router.include_router(resume.router)

# AI Prototype endpoints
api_router.include_router(prototype.router)