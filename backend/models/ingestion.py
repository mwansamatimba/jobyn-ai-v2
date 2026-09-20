"""Database models for the Jobyn Job Ingestion Engine.

Four tables:

  ingestion_sources     — registry of approved data sources with compliance state
  job_ingestion_sources — source ↔ job relationship with per-source metadata
  job_observations      — lightweight raw payload log (pruned by retention policy)
  compliance_events     — audit trail of permission / takedown events
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship
from sqlalchemy.types import JSON

from backend.database.base_class import Base
from backend.models.enums import (
    ComplianceEventType,
    PermissionStatus,
    SourceType,
    enum_column,
)
from backend.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.job import Job


# ---------------------------------------------------------------------------
# Portable JSON column — JSONB on PostgreSQL, JSON on SQLite
# ---------------------------------------------------------------------------

def _json_column():
    """Return JSONB on PostgreSQL; fall back to plain JSON elsewhere."""
    try:
        return JSONB
    except Exception:  # pragma: no cover
        return JSON


# ---------------------------------------------------------------------------
# IngestionSource
# ---------------------------------------------------------------------------


class IngestionSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registry of approved data sources for the job ingestion engine.

    A public URL is NOT automatic permission to republish content.
    Every active source must have an explicit ``permission_status`` of
    ``official_api``, ``official_feed``, or ``permission_granted`` before
    the ingestion engine will fetch from it.
    """

    __tablename__ = "ingestion_sources"

    # Identification
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Short machine-readable identifier, e.g. 'greenhouse', 'reliefweb'",
    )
    organization_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable organisation name",
    )
    source_type: Mapped[SourceType] = mapped_column(
        enum_column(SourceType),
        nullable=False,
        comment="How the source delivers data: api, rss, json, xml, …",
    )
    base_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Root URL for API/feed requests",
    )
    terms_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="URL of the source's terms of service",
    )
    robots_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="URL of robots.txt (if applicable)",
    )

    # Compliance
    legal_status: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Free-text legal notes about this source",
    )
    permission_status: Mapped[PermissionStatus] = mapped_column(
        enum_column(PermissionStatus),
        nullable=False,
        default=PermissionStatus.PERMISSION_REQUIRED,
        server_default=PermissionStatus.PERMISSION_REQUIRED.value,
        index=True,
    )
    permission_document_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Link to written permission document or API ToS acceptance",
    )
    attribution_template: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Template string for crediting the source, e.g. 'Via ReliefWeb'",
    )
    display_policy: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="How the source requires its content to be displayed",
    )

    # Rate limiting / scheduling
    retention_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=90,
        server_default=text("90"),
        comment="How long to keep raw observations before pruning",
    )
    max_requests_per_hour: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default=text("60"),
    )
    crawl_interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=720,
        server_default=text("720"),
        comment="How often to sync this source (default: every 12 hours)",
    )

    # State
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
    )
    last_successful_sync: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_attempted_sync: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    job_sources: Mapped[list[JobIngestionSource]] = orm_relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )
    observations: Mapped[list[JobObservation]] = orm_relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )
    compliance_events: Mapped[list[ComplianceEvent]] = orm_relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )

    def is_ingestion_permitted(self) -> bool:
        """Return True only when this source is explicitly cleared for ingestion."""
        return self.active and self.permission_status in {
            PermissionStatus.OFFICIAL_API,
            PermissionStatus.OFFICIAL_FEED,
            PermissionStatus.PERMISSION_GRANTED,
        }


# ---------------------------------------------------------------------------
# JobIngestionSource
# ---------------------------------------------------------------------------


class JobIngestionSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Join table: which source(s) a job was seen at, with per-source metadata.

    A single job may be listed at multiple sources (e.g. both Greenhouse and
    a direct employer RSS feed).  Each relationship is tracked independently.
    """

    __tablename__ = "job_ingestion_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_job_ingestion_sources_source_external",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="The job's ID at this source",
    )
    source_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="URL of the job at this source",
    )
    application_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Direct application URL from this source",
    )
    payload_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 hex of the normalised raw payload (detects changes)",
    )
    attribution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    first_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    last_verified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Source-specific status string",
    )

    # Relationships
    source: Mapped[IngestionSource] = orm_relationship(back_populates="job_sources")
    job: Mapped[Job] = orm_relationship(back_populates="ingestion_sources")


# ---------------------------------------------------------------------------
# JobObservation
# ---------------------------------------------------------------------------


class JobObservation(UUIDPrimaryKeyMixin, Base):
    """Lightweight raw payload log.

    Stores the raw response from a source fetch.  Pruned after
    ``IngestionSource.retention_days`` days.  Does NOT inherit TimestampMixin
    (we only care about ``created_at``; no ``updated_at`` needed).
    """

    __tablename__ = "job_observations"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        index=True,
    )
    raw_payload: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON-serialised raw payload from the source (for audit/debug)",
    )
    payload_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    http_status: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )

    # Relationships
    source: Mapped[IngestionSource] = orm_relationship(back_populates="observations")


# ---------------------------------------------------------------------------
# ComplianceEvent
# ---------------------------------------------------------------------------


class ComplianceEvent(UUIDPrimaryKeyMixin, Base):
    """Audit trail for compliance-relevant actions against a source or job.

    Immutable — never update, only insert.
    """

    __tablename__ = "compliance_events"

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[ComplianceEventType] = mapped_column(
        enum_column(ComplianceEventType),
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    requester: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Who initiated this event (user email, system identifier, …)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )

    # Relationships
    source: Mapped[IngestionSource | None] = orm_relationship(
        back_populates="compliance_events"
    )
