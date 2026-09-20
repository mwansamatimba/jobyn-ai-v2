"""Ashby public job postings API connector.

Official public endpoint — no authentication required.
Endpoint: POST https://api.ashbyhq.com/posting-api/job-board/<org>

Docs: https://developers.ashbyhq.com/reference/posting-api-overview

Configuration::

    ASHBY_BOARDS=linear,vercel,notion

Deduplication key: ``ashby:<org>:<job_id>``
Attribution: "Via Ashby / <Company>"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.http_client import ingestion_client
from backend.ingestion.sanitize import sanitize_html, validate_application_url
from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}"
_SOURCE_NAME = "ashby"
_ATTRIBUTION = "Via Ashby ({company})"

# Ashby employment type mapping
_EMPLOYMENT_MAP: dict[str, str] = {
    "fulltime": "full_time",
    "full-time": "full_time",
    "parttime": "part_time",
    "part-time": "part_time",
    "contract": "contract",
    "internship": "internship",
    "freelance": "freelance",
    "temporary": "contract",
}

# Ashby workplace type → location string hint
_WORKPLACE_MAP: dict[str, str] = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "On-site",
    "on-site": "On-site",
}


class AshbyConnector(JobSourceConnector):
    """Fetch jobs from one or more Ashby public job boards."""

    source_name = _SOURCE_NAME
    source_type = "api"
    attribution_template = "Via Ashby"

    def __init__(
        self,
        *,
        orgs: list[str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client=client)
        self._orgs = [o.strip().lower() for o in orgs if o.strip()]

    async def fetch_jobs(self) -> list[NormalizedJob]:
        if not self._orgs:
            logger.info("AshbyConnector: no orgs configured, skipping.")
            return []

        all_jobs: list[NormalizedJob] = []
        for org in self._orgs:
            try:
                jobs = await self._fetch_org(org)
                all_jobs.extend(jobs)
                logger.info("Ashby org=%s fetched=%d", org, len(jobs))
            except Exception as exc:
                logger.error("Ashby org=%s error: %s", org, exc)
        return all_jobs

    async def _fetch_org(self, org: str) -> list[NormalizedJob]:
        url = _BASE_URL.format(org=org)
        # Ashby posting-api requires a POST (empty body accepted)
        try:
            async with ingestion_client() as client:
                resp = await client.post(
                    url,
                    json={"includeCompensation": True},
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise ConnectorError(
                f"Ashby fetch failed for org {org!r}: {exc}"
            ) from exc

        # Response shape: {"results": [...], "moreDataAvailable": false}
        raw_jobs: list[dict[str, Any]] = data.get("results", [])

        results: list[NormalizedJob] = []
        for raw in raw_jobs:
            job = await self.normalize_job({**raw, "_org": org})
            if job is not None:
                results.append(job)
        return results

    async def normalize_job(self, raw: dict[str, Any]) -> NormalizedJob | None:
        org = raw.get("_org", "")

        title = _clean(raw.get("title"))
        if not title:
            self._log_skip("missing_title", raw)
            return None

        job_id = _clean(raw.get("id"))
        if not job_id:
            self._log_skip("missing_id", raw)
            return None

        app_url = validate_application_url(raw.get("jobUrl") or raw.get("applicationFormUrl"))
        if not app_url:
            self._log_skip("invalid_application_url", raw)
            return None

        # Company name
        company = _clean(raw.get("organizationName") or org.replace("-", " ").title())

        # Location
        locations: list[str] = []
        for loc in raw.get("jobLocations", []):
            if isinstance(loc, dict):
                loc_str = _clean(loc.get("locationStr") or loc.get("city") or "")
                if loc_str:
                    locations.append(loc_str)
        location = "; ".join(locations) if locations else ""

        # Workplace type
        workplace_type = _clean(raw.get("workplaceType", "")).lower()
        if workplace_type and not location:
            location = _WORKPLACE_MAP.get(workplace_type, "")

        # Employment type
        emp_raw = _clean(raw.get("employmentType", "")).lower()
        employment_type = _EMPLOYMENT_MAP.get(emp_raw, "")

        # Department
        department = _clean(raw.get("team") or raw.get("departmentName", ""))

        # Description
        description = sanitize_html(raw.get("descriptionHtml") or raw.get("description", ""))
        requirements = sanitize_html(raw.get("requirementsHtml") or "")

        # Dates
        posted_at = _parse_date(raw.get("publishedDate") or raw.get("updatedAt"))

        # Salary
        salary_min: float | None = None
        salary_max: float | None = None
        currency = "USD"
        comp = raw.get("compensation")
        if isinstance(comp, dict):
            salary_min = comp.get("minValue")
            salary_max = comp.get("maxValue")
            currency = _clean(comp.get("currency", "USD")) or "USD"

        return NormalizedJob(
            title=title,
            company=company,
            application_url=app_url,
            source_name=_SOURCE_NAME,
            external_id=f"{org}:{job_id}",
            source_url=app_url,
            attribution=_ATTRIBUTION.format(company=company),
            location=location,
            description=description,
            requirements=requirements,
            employment_type=employment_type,
            department=department,
            date_posted=posted_at,
            salary_min=float(salary_min) if salary_min is not None else None,
            salary_max=float(salary_max) if salary_max is not None else None,
            currency=currency[:3] if currency else "USD",
            raw=raw,
        )


def _clean(value: Any) -> str:
    if not value:
        return ""
    return str(value).strip()


def _parse_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            s = str(raw)[:25]
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
