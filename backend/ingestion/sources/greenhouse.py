"""Greenhouse public Job Board API connector.

Official API — no authentication required.
Docs: https://developers.greenhouse.io/job-board.html

Usage::

    # Configure boards in .env:
    GREENHOUSE_BOARDS=stripe,shopify,airbnb

    connector = GreenhouseConnector(boards=["stripe", "shopify"])
    jobs = await connector.fetch_jobs()

Deduplication key: ``greenhouse:<board_slug>:<job_id>``

Attribution: "Via Greenhouse / <Company>"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.http_client import get_json
from backend.ingestion.sanitize import sanitize_html, validate_application_url
from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_JOB_DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
_SOURCE_NAME = "greenhouse"
_ATTRIBUTION = "Via Greenhouse ({company})"


class GreenhouseConnector(JobSourceConnector):
    """Fetch jobs from one or more Greenhouse public job boards."""

    source_name = _SOURCE_NAME
    source_type = "api"
    attribution_template = "Via Greenhouse"

    def __init__(
        self,
        *,
        boards: list[str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client=client)
        self._boards = [b.strip().lower() for b in boards if b.strip()]

    async def fetch_jobs(self) -> list[NormalizedJob]:
        """Fetch all jobs from all configured Greenhouse boards."""
        if not self._boards:
            logger.info("GreenhouseConnector: no boards configured, skipping.")
            return []

        all_jobs: list[NormalizedJob] = []
        for board_slug in self._boards:
            try:
                jobs = await self._fetch_board(board_slug)
                all_jobs.extend(jobs)
                logger.info(
                    "Greenhouse board=%s fetched=%d", board_slug, len(jobs)
                )
            except Exception as exc:
                logger.error(
                    "Greenhouse board=%s error: %s", board_slug, exc
                )
                # Continue with next board
        return all_jobs

    async def _fetch_board(self, slug: str) -> list[NormalizedJob]:
        """Fetch and normalise all jobs for a single board slug."""
        url = _BASE_URL.format(slug=slug)
        try:
            data = await get_json(url, params={"content": "true"}, client=self._client)
        except Exception as exc:
            raise ConnectorError(
                f"Greenhouse fetch failed for board {slug!r}: {exc}"
            ) from exc

        raw_jobs: list[dict[str, Any]] = data.get("jobs", [])
        results: list[NormalizedJob] = []

        for raw in raw_jobs:
            job = await self.normalize_job({**raw, "_board_slug": slug})
            if job is not None:
                results.append(job)

        return results

    async def normalize_job(self, raw: dict[str, Any]) -> NormalizedJob | None:
        """Map one Greenhouse job dict to a NormalizedJob."""
        slug = raw.get("_board_slug", "")

        title = _clean(raw.get("title"))
        if not title:
            self._log_skip("missing_title", raw)
            return None

        # Company comes from the department data or board slug as fallback.
        company = _clean(raw.get("company", {}).get("name") if isinstance(raw.get("company"), dict) else None)
        if not company:
            company = slug.replace("-", " ").title()

        job_id = str(raw.get("id", ""))
        if not job_id:
            self._log_skip("missing_id", raw)
            return None

        app_url = validate_application_url(raw.get("absolute_url"))
        if not app_url:
            self._log_skip("invalid_application_url", raw)
            return None

        location_raw = raw.get("location", {})
        location = _clean(location_raw.get("name") if isinstance(location_raw, dict) else str(location_raw))

        department = ""
        departments = raw.get("departments", [])
        if departments and isinstance(departments, list):
            department = _clean(departments[0].get("name", "")) or ""

        # Content/description — requires ?content=true
        content = raw.get("content", "")
        description = sanitize_html(content)

        posted_at = _parse_date(raw.get("updated_at") or raw.get("first_published"))

        return NormalizedJob(
            title=title,
            company=company,
            application_url=app_url,
            source_name=_SOURCE_NAME,
            external_id=f"{slug}:{job_id}",
            source_url=app_url,
            attribution=_ATTRIBUTION.format(company=company),
            location=location or "",
            description=description,
            department=department,
            date_posted=posted_at,
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    if not value:
        return ""
    return str(value).strip()


def _parse_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(raw)[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
