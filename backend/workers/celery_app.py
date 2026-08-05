"""Celery application factory.

The Celery instance is created lazily by calling :func:`create_celery_app`, and
a module-level ``celery_app`` is provided for the ``celery -A`` CLI. Constructing
the instance never opens a broker connection, so importing the API does not
require Redis to be running.

Task modules should be imported by :func:`create_celery_app` (or registered via
``autodiscover_tasks``) so the worker has access to them. Keep tasks free of
FastAPI dependencies; they run outside the request lifecycle.
"""

from celery import Celery

from backend.core.config import get_settings


def create_celery_app() -> Celery:
    """Build the Celery application from application settings."""
    settings = get_settings()
    app = Celery(
        "jobyn",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
        include=["backend.workers.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=settings.CELERY_TASK_TRACK_STARTED,
        worker_prefetch_multiplier=4,
        worker_max_tasks_per_child=200,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery_app()
