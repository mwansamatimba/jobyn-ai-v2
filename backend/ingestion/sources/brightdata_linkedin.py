"""Bright Data LinkedIn Jobs discovery connector."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from backend.core.config import get_settings
from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.brightdata import BrightDataError, run_keyword_dataset
from backend.ingestion.sanitize import sanitize_html
from backend.ingestion.schema import NormalizedJob

logger = logging.getLogger(__name__)


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class BrightDataLinkedInConnector(JobSourceConnector):
    """Discover LinkedIn Jobs through Bright Data's keyword dataset."""

    source_name = "linkedin"
    source_type = "api"
    attribution_template = "Via LinkedIn"

    def __init__(
        self,
        *,
        searches: list[str] | None = None,
        limit: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client=client)
        settings = get_settings()
        self.searches = searches or [
            s.strip() for s in settings.BRIGHTDATA_SEARCHES.split(",") if s.strip()
        ]
        self.limit = limit or (
            settings.BRIGHTDATA_TEST_LIMIT if settings.BRIGHTDATA_TEST_MODE
            else settings.BRIGHTDATA_MAX_RECORDS_PER_SOURCE
        )

    async def fetch_jobs(self) -> list[NormalizedJob]:
        settings = get_settings()
        per_input = max(1, self.limit)
        inputs = [
            {
                "location": "Zambia",
                "keyword": search,
                "country": "ZM",
                "time_range": "",
                "job_type": "",
                "experience_level": "",
                "remote": "",
                "company": "",
                "selective_search": False,
                "jobs_to_not_include": "",
                "location_radius": "",
            }
            for search in self.searches
        ]
        try:
            rows = await run_keyword_dataset(
                api_key=settings.BRIGHTDATA_API_KEY or "",
                dataset_id=settings.BRIGHTDATA_LINKEDIN_DATASET_ID,
                inputs=inputs,
                limit_per_input=per_input,
                base_url=settings.BRIGHTDATA_API_BASE_URL,
                client=self._client,
                poll_interval=settings.BRIGHTDATA_POLL_INTERVAL_SECONDS,
                poll_timeout=settings.BRIGHTDATA_POLL_TIMEOUT_SECONDS,
            )
        except BrightDataError as exc:
            raise ConnectorError(str(exc)) from exc

        # A source-wide cap protects the test harness even if the provider
        # returns more than requested across multiple search inputs.
        return [job for job in (await self._normalise_rows(rows))][: self.limit]

    async def _normalise_rows(self, rows: list[dict[str, Any]]) -> list[NormalizedJob]:
        result: list[NormalizedJob] = []
        for raw in rows:
            job = await self.normalize_job(raw)
            if job is not None:
                result.append(job)
        return result

    async def normalize_job(self, raw_job: dict[str, Any]) -> NormalizedJob | None:
        try:
            title = str(_first(raw_job, "job_title", "title") or "").strip()
            company = str(_first(raw_job, "company_name", "company") or "").strip()
            source_url = str(_first(raw_job, "url", "job_url") or "").strip()
            external_id = str(_first(raw_job, "job_posting_id", "job_id", "id") or "").strip()
            location = str(_first(raw_job, "job_location", "location") or "").strip()
            description = str(
                _first(raw_job, "job_description", "job_summary", "description") or ""
            )
            application_url = str(
                _first(raw_job, "job_apply_link", "apply_link", "application_url")
                or source_url
            ).strip()
            if not title or not company or not application_url or not external_id:
                self._log_skip("missing required field", raw_job)
                return None

            return NormalizedJob(
                title=title[:255],
                company=company[:255],
                application_url=application_url[:1024],
                source_name=self.source_name,
                external_id=external_id[:512],
                source_url=source_url[:1024],
                attribution=self.attribution_template,
                location=location[:255],
                description=sanitize_html(description),
                requirements=sanitize_html(
                    str(_first(raw_job, "requirements", "qualifications") or "")
                ),
                employment_type=str(_first(raw_job, "employment_type", "job_type") or ""),
                experience_level=str(
                    _first(raw_job, "job_seniority_level", "seniority_level") or ""
                ),
                date_posted=_parse_date(
                    _first(raw_job, "date_posted", "posted_date", "job_posted_date")
                ),
                salary_min=_first(raw_job, "salary_min", "min_salary"),
                salary_max=_first(raw_job, "salary_max", "max_salary"),
                currency=str(_first(raw_job, "salary_currency", "currency") or "USD")[:3],
                raw={**raw_job, "_provider": "brightdata"},
            )
        except (TypeError, ValueError, AttributeError) as exc:
            logger.warning("Malformed LinkedIn Bright Data record: %s", exc)
            return None

    async def health_check(self) -> bool:
        try:
            return bool(await self.fetch_jobs())
        except ConnectorError:
            return False
