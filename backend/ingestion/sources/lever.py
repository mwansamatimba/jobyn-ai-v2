"""Lever public postings API connector.

Official public endpoint — no authentication required.
Endpoint: GET https://api.lever.co/v0/postings/<company>?mode=json

Docs: https://help.lever.co/hc/en-us/articles/360048559311

Configuration::

    LEVER_BOARDS=netflix,airbnb,linear

Deduplication key: ``lever:<company>:<posting_id>``
Attribution: "Via Lever / <Company>"
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

_BASE_URL = "https://api.lever.co/v0/postings/{company}?mode=json&limit=250"
_SOURCE_NAME = "lever"
_ATTRIBUTION = "Via Lever ({company})"


class LeverConnector(JobSourceConnector):
    """Fetch jobs from one or more Lever public job boards."""

    source_name = _SOURCE_NAME
    source_type = "api"
    attribution_template = "Via Lever"

    def __init__(
        self,
        *,
        companies: list[str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client=client)
        self._companies = [c.strip().lower() for c in companies if c.strip()]

    async def fetch_jobs(self) -> list[NormalizedJob]:
        if not self._companies:
            logger.info("LeverConnector: no companies configured, skipping.")
            return []

        all_jobs: list[NormalizedJob] = []
        for company in self._companies:
            try:
                jobs = await self._fetch_company(company)
                all_jobs.extend(jobs)
                logger.info("Lever company=%s fetched=%d", company, len(jobs))
            except Exception as exc:
                logger.error("Lever company=%s error: %s", company, exc)
        return all_jobs

    async def _fetch_company(self, company: str) -> list[NormalizedJob]:
        url = _BASE_URL.format(company=company)
        try:
            data = await get_json(url, client=self._client)
        except Exception as exc:
            raise ConnectorError(
                f"Lever fetch failed for {company!r}: {exc}"
            ) from exc

        # Lever returns a list of postings directly (or wrapped in data key)
        if isinstance(data, list):
            raw_jobs = data
        elif isinstance(data, dict):
            raw_jobs = data.get("data", [])
        else:
            raw_jobs = []

        results: list[NormalizedJob] = []
        for raw in raw_jobs:
            job = await self.normalize_job({**raw, "_company": company})
            if job is not None:
                results.append(job)
        return results

    async def normalize_job(self, raw: dict[str, Any]) -> NormalizedJob | None:
        company_slug = raw.get("_company", "")

        title = _clean(raw.get("text"))
        if not title:
            self._log_skip("missing_title", raw)
            return None

        posting_id = _clean(raw.get("id"))
        if not posting_id:
            self._log_skip("missing_id", raw)
            return None

        app_url = validate_application_url(raw.get("hostedUrl") or raw.get("applyUrl"))
        if not app_url:
            self._log_skip("invalid_application_url", raw)
            return None

        # Company name
        company_display = raw.get("company") or company_slug.replace("-", " ").title()

        # Location
        location_raw = raw.get("categories", {}).get("location", "") if isinstance(raw.get("categories"), dict) else ""
        location = _clean(location_raw)

        # Team / department
        team = ""
        if isinstance(raw.get("categories"), dict):
            team = _clean(raw["categories"].get("team", ""))
        if not team:
            team = _clean(raw.get("team", ""))

        # Workplace type
        workplace = ""
        if isinstance(raw.get("categories"), dict):
            workplace = _clean(raw["categories"].get("commitment", ""))

        # Description
        description_raw = ""
        if isinstance(raw.get("description"), str):
            description_raw = raw["description"]
        elif isinstance(raw.get("descriptionPlain"), str):
            description_raw = raw["descriptionPlain"]
        description = sanitize_html(description_raw)

        # Requirements / "lists" in Lever postings
        requirements_parts: list[str] = []
        for lst in raw.get("lists", []):
            if isinstance(lst, dict):
                heading = lst.get("text", "")
                content = sanitize_html(lst.get("content", ""))
                if heading:
                    requirements_parts.append(f"<strong>{heading}</strong>\n{content}")
                else:
                    requirements_parts.append(content)
        requirements = "\n\n".join(requirements_parts)

        # Posted date: Lever returns milliseconds since epoch
        posted_at: datetime | None = None
        created_at_ms = raw.get("createdAt")
        if created_at_ms:
            try:
                posted_at = datetime.fromtimestamp(
                    int(created_at_ms) / 1000, tz=timezone.utc
                )
            except (ValueError, TypeError, OSError):
                pass

        return NormalizedJob(
            title=title,
            company=company_display,
            application_url=app_url,
            source_name=_SOURCE_NAME,
            external_id=f"{company_slug}:{posting_id}",
            source_url=raw.get("hostedUrl", app_url),
            attribution=_ATTRIBUTION.format(company=company_display),
            location=location,
            description=description,
            requirements=requirements,
            department=team,
            date_posted=posted_at,
            raw=raw,
        )


def _clean(value: Any) -> str:
    if not value:
        return ""
    return str(value).strip()
