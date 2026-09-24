from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.ingestion.classifiers import classify_category, classify_location
from backend.ingestion.schema import NormalizedJob
from backend.ingestion.sync import run_sync
from backend.ingestion.sources.brightdata_indeed import BrightDataIndeedConnector
from backend.ingestion.sources.brightdata_linkedin import BrightDataLinkedInConnector
from backend.models.enums import PermissionStatus
from backend.models.ingestion import IngestionSource, JobIngestionSource, JobObservation


LINKEDIN_RAW = {
    "job_posting_id": "li-123",
    "job_title": "Software Engineer",
    "company_name": "Example Zambia",
    "job_location": "Lusaka, Zambia",
    "job_summary": "Build software.",
    "url": "https://www.linkedin.com/jobs/view/li-123",
    "date_posted": "2026-09-20T10:00:00Z",
}

INDEED_RAW = {
    "jobid": "in-123",
    "job_title": "Software Engineer",
    "company_name": "Example Zambia",
    "location": "Lusaka, Zambia",
    "description_text": "Build software.",
    "url": "https://www.indeed.com/viewjob?jk=in-123",
    "date_posted_parsed": "2026-09-20T10:00:00Z",
}


def _patch_fetch(module: str, rows):
    return patch(
        f"{module}.run_keyword_dataset",
        new=AsyncMock(return_value=rows),
    )


def test_brightdata_linkedin_parses_record():
    connector = BrightDataLinkedInConnector(searches=["Zambia jobs"], limit=20)
    result = asyncio_run(connector.normalize_job(LINKEDIN_RAW))
    assert result is not None
    assert result.title == "Software Engineer"
    assert result.company == "Example Zambia"
    assert result.external_id == "li-123"
    assert result.source_name == "linkedin"


def test_brightdata_indeed_parses_record():
    connector = BrightDataIndeedConnector(searches=["Zambia jobs"], limit=20)
    result = asyncio_run(connector.normalize_job(INDEED_RAW))
    assert result is not None
    assert result.title == "Software Engineer"
    assert result.external_id == "in-123"
    assert result.source_name == "indeed"


@pytest.mark.asyncio
async def test_brightdata_linkedin_handles_empty_response():
    connector = BrightDataLinkedInConnector(searches=["Zambia jobs"], limit=20)
    with _patch_fetch("backend.ingestion.sources.brightdata_linkedin", []):
        assert await connector.fetch_jobs() == []


@pytest.mark.asyncio
async def test_brightdata_indeed_handles_empty_response():
    connector = BrightDataIndeedConnector(searches=["Zambia jobs"], limit=20)
    with _patch_fetch("backend.ingestion.sources.brightdata_indeed", []):
        assert await connector.fetch_jobs() == []


@pytest.mark.asyncio
async def test_brightdata_handles_malformed_record():
    connector = BrightDataLinkedInConnector(searches=["Zambia jobs"], limit=20)
    assert await connector.normalize_job({"job_title": "Broken"}) is None


def test_linkedin_source_identity_is_preserved():
    result = asyncio_run(
        BrightDataLinkedInConnector(searches=["Zambia jobs"]).normalize_job(LINKEDIN_RAW)
    )
    assert result is not None
    assert result.source_name == "linkedin"
    assert result.raw["_provider"] == "brightdata"


def test_indeed_source_identity_is_preserved():
    result = asyncio_run(
        BrightDataIndeedConnector(searches=["Zambia jobs"]).normalize_job(INDEED_RAW)
    )
    assert result is not None
    assert result.source_name == "indeed"
    assert result.raw["_provider"] == "brightdata"


def test_lusaka_job_is_zambia():
    result = classify_location("Lusaka, Zambia")
    assert result.country == "Zambia"
    assert result.city == "Lusaka"
    assert result.remote_eligibility == "not_remote"


def test_remote_zambia_job_is_zambia():
    result = classify_location("Remote - Zambia")
    assert result.country == "Zambia"
    assert result.remote_eligibility == "zambia_eligible"


def test_remote_africa_is_not_zambia():
    result = classify_location("Remote - Africa")
    assert result.remote_eligibility == "africa_eligible"
    assert result.country != "Zambia"


def test_remote_global_is_not_zambia():
    result = classify_location("Remote Worldwide")
    assert result.remote_eligibility == "global"
    assert result.country != "Zambia"


def test_foreign_job_is_not_zambia():
    result = classify_location("Johannesburg, South Africa")
    assert result.country == "South Africa"
    assert result.remote_eligibility == "not_remote"


def test_existing_technician_classifier_behavior_is_preserved():
    assert classify_category("Maintenance Technician") == "skilled_trades"


def _job(source: str, external_id: str, url: str) -> NormalizedJob:
    return NormalizedJob(
        title="Software Engineer",
        company="Example Zambia",
        application_url=url,
        source_name=source,
        external_id=external_id,
        source_url=url,
        location="Lusaka, Zambia",
        description="Build software.",
        date_posted=datetime(2026, 9, 20, tzinfo=timezone.utc),
        raw={"_provider": "brightdata", "id": external_id},
    )


class _FakeConnector:
    source_type = "api"
    attribution_template = "Via Bright Data"

    def __init__(self, source_name: str, jobs: list[NormalizedJob]):
        self.source_name = source_name
        self.jobs = jobs

    async def fetch_jobs(self):
        return self.jobs

    async def normalize_job(self, raw_job):
        return None


@pytest.mark.asyncio
async def test_same_linkedin_job_is_idempotent(db_session):
    connector = _FakeConnector("linkedin", [_job(
        "linkedin", "li-1", "https://www.linkedin.com/jobs/view/shared"
    )])
    with patch("backend.ingestion.sync._build_connectors", return_value=[connector]):
        source = IngestionSource(
            name="linkedin",
            organization_name="LinkedIn",
            source_type="api",
            permission_status=PermissionStatus.PERMISSION_GRANTED,
            active=True,
        )
        db_session.add(source)
        await db_session.commit()
        first = await run_sync(db_session)
        second = await run_sync(db_session)

    assert first.total_created == 1
    assert second.total_created == 0
    assert second.total_updated == 1


@pytest.mark.asyncio
async def test_same_indeed_job_is_idempotent(db_session):
    connector = _FakeConnector("indeed", [_job(
        "indeed", "in-1", "https://www.indeed.com/viewjob?jk=shared"
    )])
    with patch("backend.ingestion.sync._build_connectors", return_value=[connector]):
        source = IngestionSource(
            name="indeed",
            organization_name="Indeed",
            source_type="api",
            permission_status=PermissionStatus.PERMISSION_GRANTED,
            active=True,
        )
        db_session.add(source)
        await db_session.commit()
        first = await run_sync(db_session)
        second = await run_sync(db_session)

    assert first.total_created == 1
    assert second.total_created == 0
    assert second.total_updated == 1


@pytest.mark.asyncio
async def test_cross_source_duplicate_uses_observation_model(db_session):
    shared_url = "https://example.com/apply/shared"
    linkedin = _FakeConnector("linkedin", [_job("linkedin", "li-shared", shared_url)])
    indeed = _FakeConnector("indeed", [_job("indeed", "in-shared", shared_url)])

    for name in ("linkedin", "indeed"):
        db_session.add(
            IngestionSource(
                name=name,
                organization_name=name.title(),
                source_type="api",
                permission_status=PermissionStatus.PERMISSION_GRANTED,
                active=True,
            )
        )
    await db_session.commit()

    with patch(
        "backend.ingestion.sync._build_connectors",
        side_effect=[[linkedin], [indeed]],
    ):
        first = await run_sync(db_session)
        second = await run_sync(db_session)

    assert first.total_created == 1
    assert second.total_created == 0

    from sqlalchemy import select
    from backend.models.job import Job

    jobs = (await db_session.execute(select(Job))).scalars().all()
    jis = (await db_session.execute(select(JobIngestionSource))).scalars().all()
    observations = (await db_session.execute(select(JobObservation))).scalars().all()

    assert len(jobs) == 1
    assert {(row.external_id, row.source_id) for row in jis}.__len__() == 2
    assert len(observations) == 2


@pytest.mark.asyncio
async def test_brightdata_source_respects_permission_gate(db_session):
    connector = _FakeConnector("linkedin", [_job(
        "linkedin", "blocked", "https://www.linkedin.com/jobs/view/blocked"
    )])
    with patch("backend.ingestion.sync._build_connectors", return_value=[connector]):
        result = await run_sync(db_session)

    assert result.sources[0].status == "error"
    assert "permission_status=permission_required" in result.sources[0].error_message


def asyncio_run(awaitable):
    import asyncio
    return asyncio.run(awaitable)
