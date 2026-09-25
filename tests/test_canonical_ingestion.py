"""Offline integration coverage for the canonical DB-backed ingestion path."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.ingestion.connectors import JobSourceConnector
from backend.ingestion.orchestrator import IngestionAuthorizationError, run_ingestion
from backend.ingestion.schema import NormalizedJob
from backend.models.ingestion import IngestionSourceRecord, JobIngestionSource, JobObservation
from backend.models.job import Job
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class FixtureConnector(JobSourceConnector):
    def __init__(self, source_name: str, jobs: list[NormalizedJob]) -> None:
        self.source_name = source_name
        self.jobs = jobs
        self.fetches = 0

    async def fetch_jobs(self) -> list[NormalizedJob]:
        self.fetches += 1
        return self.jobs


def _job(
    external_id: str,
    *,
    shared_url: bool = True,
    source_name: str = "official_api",
) -> NormalizedJob:
    return NormalizedJob(
        title="Field Technician",
        company="Fixture Zambia",
        application_url=(
            "https://fixture.invalid/apply/shared"
            if shared_url
            else f"https://fixture.invalid/apply/{external_id}"
        ),
        source_name=source_name,
        external_id=external_id,
        source_url=f"https://fixture.invalid/source/{external_id}",
        location="Lusaka, Zambia",
        country="Zambia",
        description="<p>Repair <script>alert('x')</script> equipment.</p>",
        requirements="<b>Safety training</b>",
        attribution="Fixture source",
        posted_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_canonical_pipeline_persists_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    connector = FixtureConnector("official_api", [_job("a"), _job("a")])

    first = await run_ingestion(db_session, connector)
    second = await run_ingestion(db_session, connector)

    assert connector.fetches == 2
    assert first.created == 1
    assert first.duplicates == 1
    assert second.updated == 2
    assert second.duplicates == 2
    assert await db_session.scalar(select(func.count(Job.id))) == 1
    assert await db_session.scalar(select(func.count(JobIngestionSource.id))) == 1
    assert await db_session.scalar(select(func.count(JobObservation.id))) == 4

    job = await db_session.scalar(select(Job))
    assert job is not None
    assert job.category == "skilled_trades"
    assert "<script>" not in (job.description or "")
    assert job.source_url == "https://fixture.invalid/source/a"


@pytest.mark.asyncio
async def test_canonical_key_links_same_job_without_cross_source_claim(
    db_session: AsyncSession,
) -> None:
    first = FixtureConnector("official_api", [_job("a")])
    second = FixtureConnector("official_feed", [_job("b", source_name="official_feed")])

    await run_ingestion(db_session, first)
    result = await run_ingestion(db_session, second)

    assert result.created == 0
    assert result.updated == 1
    assert await db_session.scalar(select(func.count(Job.id))) == 1
    assert await db_session.scalar(select(func.count(JobIngestionSource.id))) == 2
    source_names = await db_session.scalars(select(IngestionSourceRecord.name))
    assert set(source_names) == {"official_api", "official_feed"}


@pytest.mark.asyncio
async def test_unauthorized_source_is_rejected_before_connector_fetch(
    db_session: AsyncSession,
) -> None:
    for source_name in ("linkedin", "indeed"):
        connector = FixtureConnector(source_name, [_job("blocked")])

        with pytest.raises(IngestionAuthorizationError):
            await run_ingestion(db_session, connector)

        assert connector.fetches == 0
    assert await db_session.scalar(select(func.count(Job.id))) == 0


@pytest.mark.asyncio
async def test_invalid_and_remote_jobs_never_persist(
    db_session: AsyncSession,
) -> None:
    invalid_url = _job("invalid")
    invalid_url.application_url = "javascript:alert(1)"
    remote = _job("remote")
    remote.location = "Remote Africa"
    remote.country = ""
    connector = FixtureConnector("official_api", [invalid_url, remote])

    result = await run_ingestion(db_session, connector)

    assert result.rejected == 2
    assert await db_session.scalar(select(func.count(Job.id))) == 0


@pytest.mark.asyncio
async def test_source_name_spoofing_is_rejected(
    db_session: AsyncSession,
) -> None:
    connector = FixtureConnector("official_api", [_job("spoof", source_name="official_feed")])

    result = await run_ingestion(db_session, connector)

    assert result.rejected == 1
    assert connector.fetches == 1
    assert await db_session.scalar(select(func.count(Job.id))) == 0
