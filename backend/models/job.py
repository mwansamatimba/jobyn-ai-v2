"""Job-domain models.

``Job`` records a job posting (internal or aggregated from an external source).
``MatchResult`` captures the outcome of scoring a user's resume against a job,
and ``Application`` tracks the user's progression through a job's pipeline.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.enums import (
    ApplicationStatus,
    EmploymentType,
    ExperienceLevel,
    JobSource,
    LocationType,
    enum_column,
)
from backend.models.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from backend.models.resume import Resume
    from backend.models.user import User


class Job(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A job posting that can be matched against a candidate's resume."""

    __tablename__ = "jobs"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_type: Mapped[LocationType | None] = mapped_column(
        enum_column(LocationType),
        nullable=True,
    )
    employment_type: Mapped[EmploymentType | None] = mapped_column(
        enum_column(EmploymentType),
        nullable=True,
    )
    experience_level: Mapped[ExperienceLevel | None] = mapped_column(
        enum_column(ExperienceLevel),
        nullable=True,
    )
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        server_default="USD",
        nullable=False,
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    source: Mapped[JobSource] = mapped_column(
        enum_column(JobSource),
        default=JobSource.INTERNAL,
        server_default=JobSource.INTERNAL.value,
        nullable=False,
    )
    external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by_user: Mapped[User | None] = orm_relationship(
        back_populates="created_jobs",
        foreign_keys=[created_by_user_id],
    )
    match_results: Mapped[list[MatchResult]] = orm_relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    applications: Mapped[list[Application]] = orm_relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class MatchResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The computed match between a user's resume and a job."""

    __tablename__ = "match_results"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    matched_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    weaknesses: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    matcher_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="deterministic",
    )

    user: Mapped[User] = orm_relationship(back_populates="match_results")
    resume: Mapped[Resume | None] = orm_relationship(back_populates="match_results")
    job: Mapped[Job] = orm_relationship(back_populates="match_results")


class Application(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's application for a job and its pipeline status."""

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_column(ApplicationStatus),
        default=ApplicationStatus.DRAFT,
        server_default=ApplicationStatus.DRAFT.value,
        nullable=False,
    )
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_activity_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = orm_relationship(back_populates="applications")
    job: Mapped[Job] = orm_relationship(back_populates="applications")
    resume: Mapped[Resume | None] = orm_relationship(back_populates="applications")
