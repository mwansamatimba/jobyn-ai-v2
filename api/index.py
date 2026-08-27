"""Vercel entry point for the Jobyn AI FastAPI application.

Vercel discovers this module as the Python function entry point and serves
FastAPI's ASGI application directly.
"""

from backend.main import app

__all__ = ["app"]
