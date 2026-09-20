"""Future Zambia Partner Feed connector skeleton.

This module provides the extensible base for ingesting vacancies from
approved Zambian employers, NGOs, universities, recruiters, and public
institutions.

IMPORTANT — COMPLIANCE:
  HTML/PDF/CSV/manual ingestion MUST have a corresponding IngestionSource
  record with permission_status = 'permission_granted' before the connector
  will run.  The sync orchestrator enforces this via
  IngestionSource.is_ingestion_permitted().

  Do NOT use this connector for unapproved websites.

Supported future input formats:
  - JSON API  (subclass ZambiaPartnerConnector → override fetch_jobs)
  - CSV file  (use ZambiaCSVPartnerConnector below)
  - RSS feed  (use ZambiaRSSPartnerConnector below)
  - Manual    (use the admin API to POST jobs directly)

Usage (future)::

    connector = ZambiaPartnerConnector(
        source_name="znbc",
        company="ZNBC",
        feed_url="https://example.com/jobs.json",
    )
    jobs = await connector.fetch_jobs()
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)


class ZambiaPartnerConnector(JobSourceConnector):
    """Base class for future approved Zambia partner feed connectors.

    Subclass this and override :meth:`fetch_jobs` and :meth:`normalize_job`
    to support a specific employer or feed format.
    """

    source_name = "zambia_partner"
    source_type = "api"
    attribution_template = ""

    def __init__(
        self,
        *,
        source_name: str,
        company: str,
        feed_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client=client)
        self.source_name = source_name
        self._company = company
        self._feed_url = feed_url
        self.attribution_template = f"Via {company}"

    async def fetch_jobs(self) -> list[NormalizedJob]:
        """Override in concrete subclasses.  Returns empty list by default."""
        logger.info(
            "ZambiaPartnerConnector(%s): not yet implemented — returning empty.",
            self.source_name,
        )
        return []

    async def normalize_job(self, raw: dict[str, Any]) -> NormalizedJob | None:
        """Override in concrete subclasses."""
        return None
