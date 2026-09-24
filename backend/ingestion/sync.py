"""Job Ingestion Engine — central sync orchestrator.

This module is the single entry point for running a full synchronization.
It:
  1. Loads active configured connectors from Settings.
  2. Runs each connector independently — a failure in one never stops others.
  3. Applies location + category classifiers to each NormalizedJob.
  4. Validates the job (required fields, safe URL).
  5. Deduplicates against the database.
  6. Upserts the job (insert new / update existing).
  7. Records JobIngestionSource relationship.
  8. Updates ingestion_status / missed_syncs for jobs that disappeared.
  9. Records a lightweight JobObservation for each fetched job.
  10. Records per-source sync statistics.

A failed source logs the error and continues.  The overall sync result
contains per-source breakdown so callers can distinguish healthy vs failing
sources.

Usage::

    from backend.ingestion.sync import run_sync
    result = await run_sync(session)
    print(result.to_dict())
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.ingestion.base import ConnectorError, JobSourceConnector
from backend.ingestion.classifiers import classify_category, classify_location
from backend.ingestion.dedup import make_canonical_key, make_url_key
from backend.ingestion.expiry import compute_next_status, should_deactivate
from backend.ingestion.sanitize import validate_application_url
from backend.ingestion.schema import NormalizedJob
from backend.models.enums import (
    EmploymentType,
    ExperienceLevel,
    IngestionStatus,
    JobCategory,
    JobSource,
    LocationType,
    RemoteEligibility,
)
from backend.models.ingestion import IngestionSource, JobIngestionSource, JobObservation
from backend.models.job import Job

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-source statistics
# ---------------------------------------------------------------------------

@dataclass
class SourceSyncResult:
    source_name: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    fetched: int = 0
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    expired: int = 0
    rejected: int = 0
    errors: int = 0
    status: str = "running"
    error_message: str = ""

    @property
    def duration_seconds(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    def finish(self) -> None:
        self.completed_at = datetime.now(timezone.utc)
        if self.status == "running":
            self.status = "ok"

    def fail(self, msg: str) -> None:
        self.completed_at = datetime.now(timezone.utc)
        self.status = "error"
        self.error_message = msg

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "status": self.status,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "expired": self.expired,
            "rejected": self.rejected,
            "errors": self.errors,
            "duration_seconds": round(self.duration_seconds, 2),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }


@dataclass
class SyncResult:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    sources: list[SourceSyncResult] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def total_created(self) -> int:
        return sum(s.created for s in self.sources)

    @property
    def total_updated(self) -> int:
        return sum(s.updated for s in self.sources)

    @property
    def total_fetched(self) -> int:
        return sum(s.fetched for s in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "total_fetched": self.total_fetched,
            "total_created": self.total_created,
            "total_updated": self.total_updated,
            "sources": [s.to_dict() for s in self.sources],
        }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_sync(
    session: AsyncSession,
    *,
    source_names: list[str] | None = None,
) -> SyncResult:
    """Run a full synchronization across all configured sources.

    Args:
        session:      An async SQLAlchemy session (caller manages commit).
        source_names: If provided, only sync these specific source names.
                      Defaults to all enabled sources.

    Returns:
        A :class:`SyncResult` with per-source breakdown.
    """
    sync_result = SyncResult()
    settings = get_settings()

    if not settings.JOB_INGESTION_ENABLED:
        logger.info("JOB_INGESTION_ENABLED=false — sync skipped.")
        sync_result.completed_at = datetime.now(timezone.utc)
        return sync_result

    connectors = _build_connectors(settings, source_names)
    if not connectors:
        logger.info("No connectors configured/enabled — nothing to sync.")
        sync_result.completed_at = datetime.now(timezone.utc)
        return sync_result

    for connector in connectors:
        source_result = SourceSyncResult(source_name=connector.source_name)
        sync_result.sources.append(source_result)

        # Get or create the IngestionSource record
        db_source = await _ensure_source_record(session, connector)

        # Skip if the source is not permitted
        if not db_source.is_ingestion_permitted():
            logger.warning(
                "Source %s has permission_status=%s — skipping.",
                connector.source_name, db_source.permission_status,
            )
            source_result.fail(f"permission_status={db_source.permission_status}")
            continue

        # Track which external_ids we saw in THIS sync (for expiry)
        seen_external_ids: set[str] = set()

        try:
            jobs = await connector.fetch_jobs()
        except (ConnectorError, Exception) as exc:
            logger.error("Connector %s fetch failed: %s", connector.source_name, exc)
            source_result.fail(str(exc))
            await _update_source_last_sync(session, db_source, success=False, error=str(exc))
            await session.commit()
            continue

        source_result.fetched = len(jobs)
        sync_complete = True  # fetch succeeded → full sync

        for norm_job in jobs:
            try:
                action = await _process_job(
                    session,
                    norm_job,
                    db_source=db_source,
                    seen_external_ids=seen_external_ids,
                )
                if action == "created":
                    source_result.created += 1
                elif action == "updated":
                    source_result.updated += 1
                elif action == "duplicate":
                    source_result.duplicates += 1
                elif action == "rejected":
                    source_result.rejected += 1
            except Exception as exc:
                logger.warning(
                    "Error processing job from %s: %s",
                    connector.source_name, exc,
                )
                source_result.errors += 1

        # Apply expiry / possibly_closed logic for jobs missing from this sync
        expired_count = await _apply_expiry(
            session,
            db_source=db_source,
            seen_external_ids=seen_external_ids,
            sync_was_complete=sync_complete,
        )
        source_result.expired = expired_count

        await _update_source_last_sync(session, db_source, success=True)
        await session.commit()
        source_result.finish()

    sync_result.completed_at = datetime.now(timezone.utc)
    return sync_result


# ---------------------------------------------------------------------------
# Single job processing
# ---------------------------------------------------------------------------

async def _process_job(
    session: AsyncSession,
    norm: NormalizedJob,
    *,
    db_source: IngestionSource,
    seen_external_ids: set[str],
) -> str:
    """Validate, classify, deduplicate, and upsert one normalised job.

    Returns one of: "created", "updated", "duplicate", "rejected".
    """
    now = datetime.now(timezone.utc)

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    if not norm.title or not norm.company:
        return "rejected"

    app_url = validate_application_url(norm.application_url)
    if not app_url:
        logger.debug("Rejected job (invalid app URL): %s", norm.title[:60])
        return "rejected"
    norm.application_url = app_url

    # -----------------------------------------------------------------------
    # Location + category classification
    # -----------------------------------------------------------------------
    loc = classify_location(norm.location, country_hint=norm.country)
    category = classify_category(norm.title, norm.description)

    # -----------------------------------------------------------------------
    # Deduplication key
    # -----------------------------------------------------------------------
    canonical_key = make_canonical_key(
        source_name=norm.source_name,
        external_id=norm.external_id,
        application_url=app_url,
        company=norm.company,
        title=norm.title,
        location=norm.location,
    )

    # Track external_id for this sync (for expiry)
    if norm.external_id:
        seen_external_ids.add(norm.external_id)

    # -----------------------------------------------------------------------
    # Deduplication: check JobIngestionSource first (most reliable)
    # -----------------------------------------------------------------------
    existing_jis: JobIngestionSource | None = None
    if norm.external_id:
        result = await session.execute(
            select(JobIngestionSource).where(
                JobIngestionSource.source_id == db_source.id,
                JobIngestionSource.external_id == norm.external_id,
            ).limit(1)
        )
        existing_jis = result.scalar_one_or_none()

    existing_job: Job | None = None
    if existing_jis:
        existing_job = await session.get(Job, existing_jis.job_id)

    # Fallback: deduplicate on normalised application URL
    if existing_job is None:
        url_key = make_url_key(app_url)
        result2 = await session.execute(
            select(Job).where(
                Job.canonical_key.in_([canonical_key, url_key]),
                Job.deleted_at.is_(None),
            ).limit(1)
        )
        existing_job = result2.scalar_one_or_none()

    # Final fallback: canonical key
    if existing_job is None:
        result3 = await session.execute(
            select(Job).where(
                Job.canonical_key == canonical_key,
                Job.deleted_at.is_(None),
            ).limit(1)
        )
        existing_job = result3.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Map enums safely
    # -----------------------------------------------------------------------
    employment_type = _safe_enum(EmploymentType, norm.employment_type)
    experience_level = _safe_enum(ExperienceLevel, norm.experience_level)
    location_type = _map_location_type(loc.remote_eligibility)
    remote_eligibility = _safe_enum(RemoteEligibility, loc.remote_eligibility)
    job_category = _safe_enum(JobCategory, category)

    # -----------------------------------------------------------------------
    # Upsert
    # -----------------------------------------------------------------------
    action: str

    if existing_job is not None:
        # Update fields that may change between syncs
        existing_job.title = norm.title
        existing_job.description = norm.description or existing_job.description
        existing_job.requirements = norm.requirements or existing_job.requirements
        existing_job.location = norm.location or existing_job.location
        existing_job.location_type = location_type or existing_job.location_type
        existing_job.employment_type = employment_type or existing_job.employment_type
        existing_job.experience_level = experience_level or existing_job.experience_level
        existing_job.country = loc.country or existing_job.country
        existing_job.province = loc.province or existing_job.province
        existing_job.city = loc.city or existing_job.city
        existing_job.remote_eligibility = remote_eligibility or existing_job.remote_eligibility
        existing_job.category = job_category or existing_job.category
        existing_job.deadline = norm.deadline or existing_job.deadline
        existing_job.last_seen = now
        existing_job.last_verified = now
        existing_job.missed_syncs = 0
        existing_job.ingestion_status = IngestionStatus.ACTIVE
        existing_job.is_active = True
        if norm.salary_min is not None:
            from decimal import Decimal
            existing_job.salary_min = Decimal(str(norm.salary_min))
        if norm.salary_max is not None:
            from decimal import Decimal
            existing_job.salary_max = Decimal(str(norm.salary_max))
        await session.flush()
        action = "updated"
        job = existing_job
    else:
        from decimal import Decimal
        job = Job(
            id=uuid.uuid4(),
            title=norm.title,
            description=norm.description,
            requirements=norm.requirements,
            company_name=norm.company,
            location=norm.location,
            location_type=location_type,
            employment_type=employment_type,
            experience_level=experience_level,
            country=loc.country,
            province=loc.province,
            city=loc.city,
            remote_eligibility=remote_eligibility,
            category=job_category,
            canonical_key=canonical_key,
            external_id=norm.external_id,
            source_name=norm.source_name,
            source_url=norm.source_url or app_url,
            attribution=norm.attribution,
            external_url=app_url,
            deadline=norm.deadline,
            posted_at=norm.date_posted,
            salary_min=Decimal(str(norm.salary_min)) if norm.salary_min is not None else None,
            salary_max=Decimal(str(norm.salary_max)) if norm.salary_max is not None else None,
            salary_currency=(norm.currency or "USD")[:3],
            source=JobSource.EXTERNAL,
            is_active=True,
            ingestion_status=IngestionStatus.ACTIVE,
            first_seen=now,
            last_seen=now,
            last_verified=now,
            missed_syncs=0,
        )
        session.add(job)
        await session.flush()
        action = "created"

    # -----------------------------------------------------------------------
    # JobIngestionSource (upsert)
    # -----------------------------------------------------------------------
    import hashlib, json as _json
    payload_str = _json.dumps(
        {k: str(v) for k, v in (norm.raw or {}).items() if not isinstance(v, (dict, list))},
        sort_keys=True,
    )[:4096]
    payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()

    if existing_jis is not None:
        existing_jis.last_seen = now
        existing_jis.last_verified = now
        existing_jis.payload_hash = payload_hash
        await session.flush()
    else:
        jis = JobIngestionSource(
            id=uuid.uuid4(),
            job_id=job.id,
            source_id=db_source.id,
            external_id=norm.external_id or None,
            source_url=norm.source_url or None,
            application_url=app_url,
            payload_hash=payload_hash,
            attribution=norm.attribution or None,
            first_seen=now,
            last_seen=now,
            last_verified=now,
            status="active",
        )
        session.add(jis)
        await session.flush()

    # -----------------------------------------------------------------------
    # Raw observation (lightweight audit log)
    # -----------------------------------------------------------------------
    obs = JobObservation(
        id=uuid.uuid4(),
        source_id=db_source.id,
        external_id=norm.external_id or None,
        raw_payload=payload_str[:8192],  # store first 8 KB
        payload_hash=payload_hash,
        http_status=200,
        fetched_at=now,
    )
    session.add(obs)
    await session.flush()

    return action


# ---------------------------------------------------------------------------
# Expiry: mark jobs absent from this sync
# ---------------------------------------------------------------------------

async def _apply_expiry(
    session: AsyncSession,
    *,
    db_source: IngestionSource,
    seen_external_ids: set[str],
    sync_was_complete: bool,
) -> int:
    """Mark jobs that disappeared from this sync as possibly_closed / closed.

    Returns the number of jobs whose status changed.
    """
    now = datetime.now(timezone.utc)

    # Only process jobs that have an external_id for this source
    result = await session.execute(
        select(JobIngestionSource).where(
            JobIngestionSource.source_id == db_source.id,
            JobIngestionSource.status == "active",
        )
    )
    all_source_jobs = result.scalars().all()

    changed = 0
    for jis in all_source_jobs:
        if jis.external_id and jis.external_id in seen_external_ids:
            continue  # seen in this sync — no change

        job = await session.get(Job, jis.job_id)
        if not job or not job.is_active:
            continue

        next_status, next_missed = compute_next_status(
            current_status=str(job.ingestion_status or "active"),
            seen_in_latest_sync=False,
            sync_was_complete=sync_was_complete,
            deadline=job.deadline,
            missed_syncs=job.missed_syncs or 0,
        )

        if next_status != str(job.ingestion_status or "active"):
            job.ingestion_status = _safe_enum(IngestionStatus, next_status)
            job.missed_syncs = next_missed
            if should_deactivate(next_status):
                job.is_active = False
            await session.flush()
            changed += 1

    return changed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ensure_source_record(
    session: AsyncSession,
    connector: JobSourceConnector,
) -> IngestionSource:
    """Return or create the IngestionSource DB record for a connector."""
    result = await session.execute(
        select(IngestionSource).where(IngestionSource.name == connector.source_name).limit(1)
    )
    source = result.scalar_one_or_none()
    if source is None:
        from backend.models.enums import PermissionStatus, SourceType
        # Built-in sources get official_api permission automatically
        _OFFICIAL_SOURCES = {
            "greenhouse": PermissionStatus.OFFICIAL_API,
            "lever": PermissionStatus.OFFICIAL_API,
            "ashby": PermissionStatus.OFFICIAL_API,
            "reliefweb": PermissionStatus.OFFICIAL_API,
            "un_careers": PermissionStatus.OFFICIAL_FEED,
        }
        perm = _OFFICIAL_SOURCES.get(
            connector.source_name, PermissionStatus.PERMISSION_REQUIRED
        )
        source = IngestionSource(
            id=uuid.uuid4(),
            name=connector.source_name,
            organization_name=connector.source_name.replace("_", " ").title(),
            source_type=_safe_enum_or(SourceType, connector.source_type, SourceType.API),
            permission_status=perm,
            attribution_template=connector.attribution_template,
            active=True,
        )
        session.add(source)
        await session.flush()
    return source


async def _update_source_last_sync(
    session: AsyncSession,
    source: IngestionSource,
    *,
    success: bool,
    error: str = "",
) -> None:
    now = datetime.now(timezone.utc)
    source.last_attempted_sync = now
    if success:
        source.last_successful_sync = now
        source.last_error = None
    else:
        source.last_error = error[:1024] if error else None
    await session.flush()


def _safe_enum(enum_class: type, value: str | None) -> Any:
    """Return enum member if value is valid, else None."""
    if not value:
        return None
    try:
        return enum_class(value)
    except (ValueError, KeyError):
        return None


def _safe_enum_or(enum_class: type, value: str | None, default: Any) -> Any:
    result = _safe_enum(enum_class, value)
    return result if result is not None else default


def _map_location_type(remote_eligibility: str) -> LocationType | None:
    if remote_eligibility in {"zambia_eligible", "africa_eligible", "global", "restrictions_unclear"}:
        return LocationType.REMOTE
    if remote_eligibility == "not_remote":
        return LocationType.ONSITE
    return None


# ---------------------------------------------------------------------------
# Connector factory
# ---------------------------------------------------------------------------

def _build_connectors(
    settings: Any,
    only: list[str] | None = None,
) -> list[JobSourceConnector]:
    """Build all enabled connector instances from Settings."""
    from backend.ingestion.sources.greenhouse import GreenhouseConnector
    from backend.ingestion.sources.lever import LeverConnector
    from backend.ingestion.sources.ashby import AshbyConnector
    from backend.ingestion.sources.reliefweb import ReliefWebConnector
    from backend.ingestion.sources.un_careers import UNCareersConnector
    from backend.ingestion.sources.brightdata_linkedin import BrightDataLinkedInConnector
    from backend.ingestion.sources.brightdata_indeed import BrightDataIndeedConnector

    connectors: list[JobSourceConnector] = []

    def _want(name: str) -> bool:
        return only is None or name in only

    if settings.GREENHOUSE_ENABLED and _want("greenhouse"):
        boards = [b.strip() for b in (settings.GREENHOUSE_BOARDS or "").split(",") if b.strip()]
        if boards:
            connectors.append(GreenhouseConnector(boards=boards))

    if settings.LEVER_ENABLED and _want("lever"):
        companies = [c.strip() for c in (settings.LEVER_BOARDS or "").split(",") if c.strip()]
        if companies:
            connectors.append(LeverConnector(companies=companies))

    if settings.ASHBY_ENABLED and _want("ashby"):
        orgs = [o.strip() for o in (settings.ASHBY_BOARDS or "").split(",") if o.strip()]
        if orgs:
            connectors.append(AshbyConnector(orgs=orgs))

    if settings.RELIEFWEB_ENABLED and _want("reliefweb"):
        connectors.append(ReliefWebConnector())

    if settings.UN_CAREERS_ENABLED and _want("un_careers"):
        connectors.append(UNCareersConnector())

    if settings.BRIGHTDATA_LINKEDIN_ENABLED and _want("linkedin"):
        connectors.append(BrightDataLinkedInConnector())

    if settings.BRIGHTDATA_INDEED_ENABLED and _want("indeed"):
        connectors.append(BrightDataIndeedConnector())

    return connectors
