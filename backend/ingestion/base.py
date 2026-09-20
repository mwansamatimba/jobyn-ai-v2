"""Abstract base class for all Job Ingestion Engine source connectors.

Every source connector must subclass :class:`JobSourceConnector` and
implement:
  - :meth:`fetch_jobs`   — fetch raw data from the source
  - :meth:`normalize_job` — map one raw record to a NormalizedJob
  - :meth:`health_check` — lightweight connectivity test

Design rules
------------
* Connectors are stateless — no database sessions, no caching.
* Each connector is independent: a failure in one must not affect others.
  The sync orchestrator catches all exceptions per-connector.
* Connectors never write directly to the database.
  They return NormalizedJob instances to the sync orchestrator.
* All HTTP is done via :mod:`backend.ingestion.http_client`.
* Connectors do NOT classify location or category.
  That is handled centrally by :mod:`backend.ingestion.classifiers`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)


class ConnectorError(Exception):
    """Raised when a connector encounters a non-recoverable error."""


class JobSourceConnector(ABC):
    """Abstract base for all source connectors.

    Attributes
    ----------
    source_name:
        Short machine-readable identifier, e.g. ``"greenhouse"``.
        Must be unique across all connectors and match the corresponding
        ``IngestionSource.name`` row in the database.
    source_type:
        ``SourceType`` enum value string.  Used for logging.
    attribution_template:
        Default attribution string.  Persisted to the ``attribution`` column
        on ``Job`` rows created by this connector.
    """

    source_name: str = ""
    source_type: str = "api"
    attribution_template: str = ""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client  # optional injected client (for tests)

    @abstractmethod
    async def fetch_jobs(self) -> list[NormalizedJob]:
        """Fetch and normalise all available jobs from this source.

        Returns:
            A list of :class:`NormalizedJob` instances.  May be empty.

        Raises:
            ConnectorError: On any non-recoverable fetch or parse error.
        """

    @abstractmethod
    async def normalize_job(self, raw_job: dict[str, Any]) -> NormalizedJob | None:
        """Map one raw source record to a :class:`NormalizedJob`.

        Returns None if the record is invalid or should be skipped.
        Must never raise — return None on any parse error.
        """

    async def health_check(self) -> bool:
        """Lightweight connectivity check.

        Default implementation calls :meth:`fetch_jobs` and returns True if
        at least one job is returned without an exception.

        Override to use a cheaper endpoint (e.g. a /ping or first-page-only
        request).

        Returns:
            True if the source is reachable and returning data.
        """
        try:
            jobs = await self.fetch_jobs()
            return len(jobs) > 0
        except Exception as exc:
            logger.warning("%s health check failed: %s", self.source_name, exc)
            return False

    def _log_skip(self, reason: str, raw: dict[str, Any]) -> None:
        """Log a skipped record at DEBUG level without logging raw content."""
        logger.debug(
            "Skipping %s record (reason=%s external_id=%s)",
            self.source_name,
            reason,
            raw.get("id") or raw.get("uid") or "?",
        )
