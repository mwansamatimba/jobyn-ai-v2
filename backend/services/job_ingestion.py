"""Job ingestion service — Remotive public API.

Fetches remote job listings from the Remotive public jobs API (no API key
required) and normalises them into the existing ``Job`` model.

Source: https://remotive.com/api/remote-jobs
Endpoint: GET https://remotive.com/api/remote-jobs?category=software-dev&limit=50

Deduplication: a job is skipped if a row with the same ``external_url`` already
exists in the ``jobs`` table (regardless of soft-delete state).

Run manually:
    .venv312\\Scripts\\python.exe -m backend.services.job_ingestion

Or from code:
    from backend.services.job_ingestion import ingest_jobs
    result = asyncio.run(ingest_jobs())
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.session import async_session_factory
from backend.models.enums import EmploymentType, ExperienceLevel, JobSource, LocationType
from backend.models.job import Job
from backend.repositories.job import JobRepository

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Source configuration                                                 #
# ------------------------------------------------------------------ #

_REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
_DEFAULT_CATEGORY = "software-dev"
_DEFAULT_LIMIT = 50
_REQUEST_TIMEOUT = 30.0

# ------------------------------------------------------------------ #
# Field normalisation maps                                             #
# ------------------------------------------------------------------ #

_EMPLOYMENT_TYPE_MAP: dict[str, EmploymentType] = {
    "full_time": EmploymentType.FULL_TIME,
    "full-time": EmploymentType.FULL_TIME,
    "contract": EmploymentType.CONTRACT,
    "freelance": EmploymentType.FREELANCE,
    "part_time": EmploymentType.PART_TIME,
    "part-time": EmploymentType.PART_TIME,
    "internship": EmploymentType.INTERNSHIP,
}

_EXPERIENCE_LEVEL_KEYWORDS: list[tuple[list[str], ExperienceLevel]] = [
    (["principal", "staff", "executive", "vp ", "director"], ExperienceLevel.EXECUTIVE),
    (["lead", "head of", "tech lead"], ExperienceLevel.LEAD),
    (["senior", "sr.", "sr "], ExperienceLevel.SENIOR),
    (["junior", "jr.", "jr "], ExperienceLevel.JUNIOR),
    (["entry", "intern", "graduate", "associate"], ExperienceLevel.ENTRY),
]

# ------------------------------------------------------------------ #
# Result dataclass                                                     #
# ------------------------------------------------------------------ #


class IngestionResult:
    """Statistics returned after an ingestion run."""

    __slots__ = ("source", "fetched", "created", "duplicates", "invalid", "errors")

    def __init__(self) -> None:
        self.source = "remotive"
        self.fetched = 0
        self.created = 0
        self.duplicates = 0
        self.invalid = 0
        self.errors = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched": self.fetched,
            "created": self.created,
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "errors": self.errors,
        }

    def __repr__(self) -> str:
        return (
            f"IngestionResult(fetched={self.fetched}, created={self.created}, "
            f"duplicates={self.duplicates}, invalid={self.invalid}, "
            f"errors={self.errors})"
        )


# ------------------------------------------------------------------ #
# Normalisation helpers                                                #
# ------------------------------------------------------------------ #


def _clean_text(value: str | None) -> str | None:
    """Strip and collapse whitespace; return None for blank strings."""
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value.strip())
    return cleaned or None


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return value[:max_len]


def _normalise_employment_type(raw: str | None) -> EmploymentType | None:
    if not raw:
        return None
    return _EMPLOYMENT_TYPE_MAP.get(raw.strip().lower())


def _infer_experience_level(title: str) -> ExperienceLevel | None:
    """Infer experience level from the job title using keyword matching."""
    lower = title.lower()
    for keywords, level in _EXPERIENCE_LEVEL_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return level
    return ExperienceLevel.MID  # sensible default for unlabelled roles


def _normalise_location_type(location: str | None) -> LocationType:
    """Remotive jobs are all remote; still normalise from description."""
    if not location:
        return LocationType.REMOTE
    lower = location.lower()
    if "hybrid" in lower:
        return LocationType.HYBRID
    if "onsite" in lower or "on-site" in lower or "office" in lower:
        return LocationType.ONSITE
    return LocationType.REMOTE


def _parse_posted_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalise_remotive_job(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a single Remotive API job dict into Job model kwargs.

    Returns None if the required fields (title, company, url) are missing.
    """
    title = _clean_text(raw.get("title"))
    company = _clean_text(raw.get("company_name"))
    url = _clean_text(raw.get("url"))

    if not title or not company or not url:
        return None

    description = _clean_text(raw.get("description")) or ""
    location = _clean_text(raw.get("candidate_required_location")) or "Worldwide"
    employment_type = _normalise_employment_type(raw.get("job_type"))
    experience_level = _infer_experience_level(title)
    location_type = _normalise_location_type(location)
    posted_at = _parse_posted_at(raw.get("publication_date"))

    return {
        "title": _truncate(title, 255),
        "company_name": _truncate(company, 255),
        "description": description,
        "location": _truncate(location, 255),
        "location_type": location_type,
        "employment_type": employment_type,
        "experience_level": experience_level,
        "external_url": _truncate(url, 512),
        "source": JobSource.EXTERNAL,
        "is_active": True,
        "posted_at": posted_at,
        "salary_currency": "USD",
    }


# ------------------------------------------------------------------ #
# Fetch                                                                #
# ------------------------------------------------------------------ #


async def fetch_remotive_jobs(
    *,
    category: str = _DEFAULT_CATEGORY,
    limit: int = _DEFAULT_LIMIT,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch job listings from the Remotive public API.

    Args:
        category: Remotive category slug.
        limit: Maximum number of jobs to request.
        client: Optional pre-configured HTTPX client (useful for testing).

    Returns:
        A list of raw job dicts from the API response.

    Raises:
        httpx.HTTPError: On network or HTTP-level failure.
    """
    params = {"category": category, "limit": limit}
    _client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)
    try:
        response = await _client.get(_REMOTIVE_URL, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("jobs", [])
    finally:
        if client is None:
            await _client.aclose()


# ------------------------------------------------------------------ #
# Ingestion orchestration                                              #
# ------------------------------------------------------------------ #


async def ingest_jobs(
    *,
    category: str = _DEFAULT_CATEGORY,
    limit: int = _DEFAULT_LIMIT,
    http_client: httpx.AsyncClient | None = None,
    session: AsyncSession | None = None,
) -> IngestionResult:
    """Fetch and persist job listings into the existing ``jobs`` table.

    Args:
        category: Remotive category to ingest.
        limit: Number of jobs to fetch from the API.
        http_client: Optional HTTPX client (injected in tests to avoid live calls).
        session: Optional async session (injected in tests).

    Returns:
        An :class:`IngestionResult` with counts of fetched/created/duplicate jobs.
    """
    result = IngestionResult()

    # Fetch raw jobs.
    try:
        raw_jobs = await fetch_remotive_jobs(
            category=category, limit=limit, client=http_client
        )
    except Exception as exc:
        logger.error("Failed to fetch jobs from Remotive: %s", exc)
        result.errors += 1
        return result

    result.fetched = len(raw_jobs)
    logger.info("Fetched %d raw jobs from Remotive.", result.fetched)

    # Open or reuse a database session.
    _own_session = session is None
    _session: AsyncSession = session or async_session_factory()  # type: ignore[assignment]

    try:
        if _own_session:
            async with async_session_factory() as _session:
                await _process_jobs(raw_jobs, _session, result)
        else:
            await _process_jobs(raw_jobs, _session, result)
    except Exception as exc:
        logger.exception("Ingestion failed with an unexpected error: %s", exc)
        result.errors += 1

    logger.info("Ingestion complete: %r", result)
    return result


async def _process_jobs(
    raw_jobs: list[dict[str, Any]],
    session: AsyncSession,
    result: IngestionResult,
) -> None:
    """Normalise, deduplicate and persist a batch of raw job dicts."""
    repo = JobRepository(session=session, model=Job)

    for raw in raw_jobs:
        try:
            normalised = normalise_remotive_job(raw)
        except Exception as exc:
            logger.warning("Failed to normalise job: %s — %s", raw.get("title"), exc)
            result.invalid += 1
            continue

        if normalised is None:
            result.invalid += 1
            continue

        # Deduplication: skip if this URL is already in the database.
        external_url = normalised.get("external_url")
        if external_url:
            existing = await repo.get_by_external_url(external_url)
            if existing is not None:
                result.duplicates += 1
                continue

        try:
            await repo.create(**normalised)
            await session.commit()
            result.created += 1
        except Exception as exc:
            await session.rollback()
            logger.warning("Failed to persist job '%s': %s", normalised.get("title"), exc)
            result.errors += 1


# ------------------------------------------------------------------ #
# CLI entry point                                                      #
# ------------------------------------------------------------------ #


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    result = await ingest_jobs()
    print(result.to_dict())


if __name__ == "__main__":
    asyncio.run(_main())
