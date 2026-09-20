"""ReliefWeb jobs API connector.

Official UN OCHA humanitarian information API — no key required.
Endpoint: https://api.reliefweb.int/v1/jobs

API docs: https://apidoc.rwlabs.org/

ReliefWeb is a high-priority source for Jobyn because it covers:
  - Zambia and Southern Africa
  - NGO / humanitarian / development sector
  - Consultancy, internship, and remote opportunities

This connector does NOT hardcode Zambia-only filtering.  It returns
all available jobs; location classification is handled centrally by
:mod:`backend.ingestion.classifiers`.

Configuration::

    RELIEFWEB_ENABLED=true

Rate limit: 1000 requests/day (unauthenticated) — well within normal use.
Attribution: "Via ReliefWeb (https://reliefweb.int)"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.http_client import get_json
from backend.ingestion.sanitize import html_to_text, sanitize_html, validate_application_url
from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)

_API_URL = "https://api.reliefweb.int/v1/jobs"
_SOURCE_NAME = "reliefweb"
_ATTRIBUTION = "Via ReliefWeb (https://reliefweb.int)"
_PAGE_SIZE = 100   # max allowed by the API
_MAX_PAGES = 10    # safety cap: 1 000 jobs per sync


class ReliefWebConnector(JobSourceConnector):
    """Fetch humanitarian/development jobs from ReliefWeb."""

    source_name = _SOURCE_NAME
    source_type = "api"
    attribution_template = _ATTRIBUTION

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client=client)

    async def fetch_jobs(self) -> list[NormalizedJob]:
        all_jobs: list[NormalizedJob] = []
        offset = 0

        for _ in range(_MAX_PAGES):
            page_jobs, total = await self._fetch_page(offset)
            all_jobs.extend(page_jobs)
            offset += _PAGE_SIZE
            if offset >= total or not page_jobs:
                break

        logger.info("ReliefWeb fetched=%d total", len(all_jobs))
        return all_jobs

    async def _fetch_page(
        self, offset: int
    ) -> tuple[list[NormalizedJob], int]:
        payload: dict[str, Any] = {
            "fields": {
                "include": [
                    "id", "title", "source", "country",
                    "city", "theme", "type",
                    "date", "body", "how_to_apply",
                    "url", "job_closing_date",
                    "career_categories",
                ]
            },
            "filter": {
                "operator": "AND",
                "conditions": [
                    {"field": "status", "value": "published"},
                ],
            },
            "sort": [{"field": "date.created", "value": "desc"}],
            "limit": _PAGE_SIZE,
            "offset": offset,
        }

        try:
            data = await get_json(
                _API_URL,
                client=self._client,
            )
        except Exception as exc:
            # Use POST-style — ReliefWeb supports both GET+params and POST+JSON
            try:
                from backend.ingestion.http_client import ingestion_client
                async with ingestion_client() as c:
                    resp = await c.post(
                        _API_URL,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as exc2:
                raise ConnectorError(
                    f"ReliefWeb fetch failed: {exc2}"
                ) from exc2

        total = data.get("totalCount", 0) or data.get("count", 0)
        raw_jobs: list[dict[str, Any]] = data.get("data", [])

        results: list[NormalizedJob] = []
        for item in raw_jobs:
            job = await self.normalize_job(item)
            if job is not None:
                results.append(job)

        return results, int(total)

    async def normalize_job(self, raw: dict[str, Any]) -> NormalizedJob | None:
        fields = raw.get("fields", raw)  # API wraps in 'fields'

        external_id = str(raw.get("id") or fields.get("id") or "")
        if not external_id:
            self._log_skip("missing_id", raw)
            return None

        title = _clean(fields.get("title"))
        if not title:
            self._log_skip("missing_title", raw)
            return None

        # Organisation (ReliefWeb "source")
        sources = fields.get("source", [])
        company = ""
        if isinstance(sources, list) and sources:
            company = _clean(sources[0].get("name", ""))
        elif isinstance(sources, dict):
            company = _clean(sources.get("name", ""))
        if not company:
            company = "Unknown Organisation"

        # Application URL: prefer how_to_apply link, fall back to ReliefWeb page
        how_to_apply_raw = fields.get("how_to_apply", "")
        app_url: str | None = None
        if how_to_apply_raw:
            # Try to extract first URL from the HTML/text
            import re
            url_match = re.search(r'https?://[^\s"\'<>]+', how_to_apply_raw)
            if url_match:
                app_url = validate_application_url(url_match.group(0))
        if not app_url:
            app_url = validate_application_url(fields.get("url"))
        if not app_url:
            self._log_skip("missing_application_url", raw)
            return None

        source_url = _clean(fields.get("url", app_url))

        # Location
        country = ""
        countries = fields.get("country", [])
        if isinstance(countries, list) and countries:
            country = _clean(countries[0].get("name", ""))
        elif isinstance(countries, dict):
            country = _clean(countries.get("name", ""))

        city = _clean(fields.get("city", ""))
        location = city or country

        # Description / body
        body = fields.get("body", "")
        description = sanitize_html(body) if body else ""

        # How to apply → requirements
        requirements = html_to_text(how_to_apply_raw) if how_to_apply_raw else ""

        # Employment type hint from job type
        job_types = fields.get("type", [])
        employment_type = ""
        if isinstance(job_types, list) and job_types:
            t = _clean(job_types[0].get("name", "")).lower()
            if "internship" in t:
                employment_type = "internship"
            elif "volunteer" in t or "consultancy" in t:
                employment_type = "contract"

        # Dates
        date_info = fields.get("date", {})
        if isinstance(date_info, dict):
            posted_at = _parse_rw_date(date_info.get("created") or date_info.get("changed"))
        else:
            posted_at = None

        deadline = _parse_rw_date(fields.get("job_closing_date"))

        return NormalizedJob(
            title=title,
            company=company,
            application_url=app_url,
            source_name=_SOURCE_NAME,
            external_id=external_id,
            source_url=source_url,
            attribution=_ATTRIBUTION,
            location=location,
            country=country,
            city=city,
            description=description,
            requirements=requirements,
            employment_type=employment_type,
            date_posted=posted_at,
            deadline=deadline,
            raw=raw,
        )


def _clean(value: Any) -> str:
    if not value:
        return ""
    return str(value).strip()


def _parse_rw_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(raw)[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
