"""Celery task definitions.

Feature tasks (resume parsing, matching, cover letter generation, etc.) are
added here in their own modules under ``backend/workers/`` and registered in
the ``include`` list of :func:`backend.workers.celery_app.create_celery_app`.

The ``ping`` task below exists only to verify that the broker, worker, and
result backend are wired correctly.
"""

from backend.workers.celery_app import celery_app


@celery_app.task(name="workers.ping", bind=True)
def ping(self) -> dict[str, str]:
    """Return a fixed payload to validate Celery wiring."""
    return {"status": "pong", "task_id": self.request.id}
