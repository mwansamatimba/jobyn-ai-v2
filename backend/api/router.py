"""Aggregate API router for Jobyn AI."""

from fastapi import APIRouter

from backend.api.routes import application_copilot
from backend.api.routes import applications
from backend.api.routes import auth
from backend.api.routes import career
from backend.api.routes import jobs
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

# Job discovery and matching endpoints
api_router.include_router(jobs.router)

# Career navigator and skill gap analysis endpoints
api_router.include_router(career.router)

# AI Application Copilot — cover letter generation
api_router.include_router(application_copilot.router)

# Application tracking engine
api_router.include_router(applications.router)

# AI Prototype endpoints
api_router.include_router(prototype.router)