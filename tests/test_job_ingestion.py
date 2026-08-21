"""Tests for the job ingestion service.

All external HTTP calls are mocked — no live network requests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import httpx

from backend.services.job_ingestion import (
    IngestionResult,
    fetch_remotive_jobs,
    ingest_jobs,
    normalise_remotive_job,
)

# ------------------------------------------------------------------ #
# Sample data                                                          #
# ------------------------------------------------------------------ #

_VALID_RAW: dict[str, Any] = {
    "title": "Senior Backend Engineer",
    "company_name": "Acme Corp",
    "url": "https://remotive.com/jobs/12345",
    "description": "Build scalable APIs with Python.",
    "candidate_required_location": "Worldwide",
    "job_type": "full_time",
    "publication_date": "2026-07-01T10:00:00",
}

_VALID_RAW_2: dict[str, Any] = {
    "title": "Junior Frontend Developer",
    "company_name": "Beta Ltd",
    "url": "https://remotive.com/jobs/99999",
    "description": "React and TypeScript role.",
    "candidate_required_location": "Europe",
    "job_type": "full_time",
    "publication_date": "2026-07-15T09:00:00",
}

_MISSING_TITLE: dict[str, Any] = {
    "title": "",
    "company_name": "NoCo",
    "url": "https://remotive.com/jobs/bad1",
}

_MISSING_COMPANY: dict[str, Any] = {
    "title": "Engineer",
    "company_name": "",
    "url": "https://remotive.com/jobs/bad2",
}

_MISSING_URL: dict[str, Any] = {
    "title": "Engineer",
    "company_name": "AnyComp",
    "url": "",
}


# ------------------------------------------------------------------ #
# Helper — build a mock httpx response                                 #
# ------------------------------------------------------------------ #

def _mock_http_response(jobs: list[dict]) -> httpx.Response:
    """Return a mock httpx.Response containing the given jobs."""
    content = json.dumps({"jobs": jobs}).encode()
    response = httpx.Response(
        status_code=200,
        content=content,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://remotive.com/api/remote-jobs"),
    )
    return response


# ------------------------------------------------------------------ #
# normalise_remotive_job unit tests                                    #
# ------------------------------------------------------------------ #

def test_normalise_valid_job():
    result = normalise_remotive_job(_VALID_RAW)
    assert result is not None
    assert result["title"] == "Senior Backend Engineer"
    assert result["company_name"] == "Acme Corp"
    assert result["external_url"] == "https://remotive.com/jobs/12345"
    assert result["source"] == "external"
    assert result["is_active"] is True
    assert result["location_type"] is not None
    assert result["employment_type"] is not None
    assert result["experience_level"] is not None


def test_normalise_infers_senior_from_title():
    raw = {**_VALID_RAW, "title": "Senior Python Engineer"}
    result = normalise_remotive_job(raw)
    assert result is not None
    from backend.models.enums import ExperienceLevel
    assert result["experience_level"] == ExperienceLevel.SENIOR


def test_normalise_infers_junior_from_title():
    raw = {**_VALID_RAW, "title": "Junior Backend Developer"}
    result = normalise_remotive_job(raw)
    assert result is not None
    from backend.models.enums import ExperienceLevel
    assert result["experience_level"] == ExperienceLevel.JUNIOR


def test_normalise_missing_title_returns_none():
    assert normalise_remotive_job(_MISSING_TITLE) is None


def test_normalise_missing_company_returns_none():
    assert normalise_remotive_job(_MISSING_COMPANY) is None


def test_normalise_missing_url_returns_none():
    assert normalise_remotive_job(_MISSING_URL) is None


def test_normalise_maps_full_time():
    raw = {**_VALID_RAW, "job_type": "full_time"}
    result = normalise_remotive_job(raw)
    assert result is not None
    from backend.models.enums import EmploymentType
    assert result["employment_type"] == EmploymentType.FULL_TIME


def test_normalise_maps_contract():
    raw = {**_VALID_RAW, "job_type": "contract"}
    result = normalise_remotive_job(raw)
    assert result is not None
    from backend.models.enums import EmploymentType
    assert result["employment_type"] == EmploymentType.CONTRACT


def test_normalise_remote_location_type():
    raw = {**_VALID_RAW, "candidate_required_location": "Worldwide"}
    result = normalise_remotive_job(raw)
    assert result is not None
    from backend.models.enums import LocationType
    assert result["location_type"] == LocationType.REMOTE


def test_normalise_collapses_whitespace():
    raw = {**_VALID_RAW, "title": "  Senior   Backend  Engineer  "}
    result = normalise_remotive_job(raw)
    assert result is not None
    assert result["title"] == "Senior Backend Engineer"


def test_normalise_posted_at_parsed():
    result = normalise_remotive_job(_VALID_RAW)
    assert result is not None
    assert result["posted_at"] is not None


def test_normalise_missing_publication_date_ok():
    raw = {**_VALID_RAW}
    del raw["publication_date"]
    result = normalise_remotive_job(raw)
    assert result is not None
    assert result["posted_at"] is None


# ------------------------------------------------------------------ #
# ingest_jobs integration tests (all HTTP + DB mocked)                #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_ingest_creates_new_jobs(db_session):
    """Two valid jobs with unique URLs are both inserted."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        return_value=_mock_http_response([_VALID_RAW, _VALID_RAW_2])
    )

    result = await ingest_jobs(http_client=mock_client, session=db_session)

    assert result.fetched == 2
    assert result.created == 2
    assert result.duplicates == 0
    assert result.invalid == 0


@pytest.mark.asyncio
async def test_ingest_skips_invalid_jobs(db_session):
    """Jobs missing required fields are counted as invalid and skipped."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        return_value=_mock_http_response([_MISSING_TITLE, _MISSING_COMPANY, _VALID_RAW])
    )

    result = await ingest_jobs(http_client=mock_client, session=db_session)

    assert result.fetched == 3
    assert result.created == 1
    assert result.invalid == 2


@pytest.mark.asyncio
async def test_ingest_deduplicates_on_repeated_run(db_session):
    """Running ingestion twice with the same data creates 0 new jobs on second run."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        return_value=_mock_http_response([_VALID_RAW])
    )

    first = await ingest_jobs(http_client=mock_client, session=db_session)
    assert first.created == 1

    # Reset mock return value for second call.
    mock_client.get = AsyncMock(
        return_value=_mock_http_response([_VALID_RAW])
    )
    second = await ingest_jobs(http_client=mock_client, session=db_session)
    assert second.created == 0
    assert second.duplicates == 1


@pytest.mark.asyncio
async def test_ingest_handles_http_failure_gracefully(db_session):
    """A network error is captured; result.errors is incremented."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        side_effect=httpx.ConnectError("unreachable")
    )

    result = await ingest_jobs(http_client=mock_client, session=db_session)

    assert result.errors >= 1
    assert result.created == 0


@pytest.mark.asyncio
async def test_ingest_returns_stats_dict(db_session):
    """to_dict() returns all expected keys."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        return_value=_mock_http_response([_VALID_RAW])
    )

    result = await ingest_jobs(http_client=mock_client, session=db_session)
    d = result.to_dict()

    for key in ("source", "fetched", "created", "duplicates", "invalid", "errors"):
        assert key in d, f"missing key: {key}"
    assert d["source"] == "remotive"


@pytest.mark.asyncio
async def test_ingest_multiple_jobs_all_unique(db_session):
    """Multiple unique jobs are all ingested without duplicates."""
    jobs = [
        {**_VALID_RAW, "url": f"https://remotive.com/jobs/{i}", "title": f"Job {i}"}
        for i in range(10)
    ]
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=_mock_http_response(jobs))

    result = await ingest_jobs(http_client=mock_client, session=db_session)

    assert result.fetched == 10
    assert result.created == 10
    assert result.duplicates == 0
