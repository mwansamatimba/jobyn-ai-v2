"""ORM records used by the canonical ingestion pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base_class import Base
from backend.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class IngestionSourceRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted source compliance state; retrieval is allowed only when permitted."""

    __tablename__ = "ingestion_sources"
    __table_args__ = (UniqueConstraint("name", name="uq_ingestion_sources_name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    organization_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    terms_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    robots_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    legal_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_document_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    attribution_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_status: Mapped[str] = mapped_column(
        String(32), default="permission_required", server_default="permission_required",
        nullable=False, index=True,
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False, index=True
    )
    retention_days: Mapped[int] = mapped_column(
        Integer, default=90, server_default="90", nullable=False
    )
    max_requests_per_hour: Mapped[int] = mapped_column(
        Integer, default=60, server_default="60", nullable=False
    )
    crawl_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=720, server_default="720", nullable=False
    )
    last_successful_sync: Mapped[datetime | None] = mapped_column(nullable=True)
    last_attempted_sync: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    jobs: Mapped[list[JobIngestionSource]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    observations: Mapped[list[JobObservation]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    def is_ingestion_permitted(self) -> bool:
        """Apply the production authorization contract without environment bypasses."""

        return self.active and self.permission_status in {
            "official_api",
            "official_feed",
            "permission_granted",
        }


class JobIngestionSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Source-specific identity and attribution for a canonical job."""

    __tablename__ = "job_ingestion_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "external_id", name="uq_job_ingestion_sources_source_external"
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    application_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(nullable=True)
    last_verified: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    job: Mapped[Job] = relationship(back_populates="ingestion_sources")
    source: Mapped[IngestionSourceRecord] = relationship(back_populates="jobs")


class JobObservation(UUIDPrimaryKeyMixin, Base):
    """Immutable observation metadata retained for source auditability."""

    __tablename__ = "job_observations"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[IngestionSourceRecord] = relationship(back_populates="observations")


from backend.models.job import Job  # noqa: E402
