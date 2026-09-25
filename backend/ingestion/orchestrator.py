"""Canonical async ingestion orchestration and persistence entry point."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ingestion.connectors import JobSourceConnector
from backend.ingestion.processing import prepare_job
from backend.ingestion.sources import get_source
from backend.models.enums import JobSource
from backend.models.ingestion import IngestionSourceRecord, JobIngestionSource, JobObservation
from backend.models.job import Job


class IngestionAuthorizationError(PermissionError):
    """Raised before connector retrieval when source authorization is insufficient."""


@dataclass(slots=True)
class IngestionRunResult:
    source_name: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    rejected: int = 0
    observations: int = 0


async def _source_record(session: AsyncSession, source_name: str) -> IngestionSourceRecord:
    policy = get_source(source_name)
    if policy is None:
        raise IngestionAuthorizationError(f"unknown ingestion source: {source_name}")
    result = await session.execute(
        select(IngestionSourceRecord).where(IngestionSourceRecord.name == policy.name)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = IngestionSourceRecord(
            name=policy.name,
            organization_name=policy.name,
            source_type="connector",
            permission_status=policy.permission_status.value,
            active=policy.active,
        )
        session.add(record)
        await session.flush()
    return record


async def run_ingestion(
    session: AsyncSession,
    connector: JobSourceConnector,
) -> IngestionRunResult:
    """Fetch, process, and persist one authorized connector's normalized jobs."""

    source = await _source_record(session, connector.source_name)
    if not source.is_ingestion_permitted():
        await session.rollback()
        raise IngestionAuthorizationError(
            f"source {connector.source_name} is not authorized for ingestion"
        )

    jobs = await connector.fetch_jobs()
    result = IngestionRunResult(source_name=connector.source_name, fetched=len(jobs))
    now = datetime.now(UTC)
    source.last_attempted_sync = now

    for normalized in jobs:
        if normalized.source_name != connector.source_name:
            result.rejected += 1
            continue
        prepared = prepare_job(normalized)
        if prepared is None:
            result.rejected += 1
            continue
        job_data, key = prepared
        existing_link = None
        if job_data.external_id:
            link_result = await session.execute(
                select(JobIngestionSource).where(
                    JobIngestionSource.source_id == source.id,
                    JobIngestionSource.external_id == job_data.external_id,
                )
            )
            existing_link = link_result.scalar_one_or_none()
        job = await session.get(Job, existing_link.job_id) if existing_link else None
        if job is None:
            job_result = await session.execute(select(Job).where(Job.canonical_key == key))
            job = job_result.scalar_one_or_none()

        if job is None:
            job = Job(
                title=job_data.title,
                company_name=job_data.company,
                description=job_data.description,
                location=job_data.location,
                posted_at=job_data.posted_at,
                expires_at=job_data.deadline,
                source_name=job_data.source_name,
                source_url=job_data.source_url,
                external_url=job_data.application_url,
                source=JobSource.EXTERNAL,
                external_id=job_data.external_id,
                canonical_key=key,
                attribution=job_data.attribution,
                country=job_data.country,
                province=job_data.province,
                city=job_data.city,
                remote_eligibility=job_data.remote_eligibility,
                category=job_data.category,
                requirements=job_data.requirements,
                first_seen=now,
                last_seen=now,
                last_verified=now,
            )
            session.add(job)
            await session.flush()
            result.created += 1
        else:
            job.last_seen = now
            job.last_verified = now
            result.updated += 1

        payload = json.dumps(job_data.raw, sort_keys=True, default=str)
        payload_hash = sha256(payload.encode()).hexdigest()
        if existing_link is None:
            session.add(
                JobIngestionSource(
                    job_id=job.id,
                    source_id=source.id,
                    external_id=job_data.external_id or None,
                    source_url=job_data.source_url or None,
                    application_url=job_data.application_url,
                    payload_hash=payload_hash,
                    attribution=job_data.attribution,
                    first_seen=now,
                    last_seen=now,
                    last_verified=now,
                    status="active",
                )
            )
        else:
            existing_link.last_seen = now
            existing_link.last_verified = now
            result.duplicates += 1
        await session.flush()
        session.add(
            JobObservation(
                source_id=source.id,
                external_id=job_data.external_id or None,
                raw_payload=payload,
                payload_hash=payload_hash,
                fetched_at=now,
            )
        )
        result.observations += 1

    source.last_successful_sync = now
    await session.commit()
    return result
