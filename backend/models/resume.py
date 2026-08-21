"""Resume-domain models.

``Resume`` is the canonical resume document derived from an AI parse. The other
tables are supporting records that reference the canonical resume and, in the
case of uploads, the owning user.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.enums import (
    DraftStatus,
    GenerationStatus,
    ParseStatus,
    ResumeStatus,
    enum_column,
)
from backend.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.career import CareerInsight
    from backend.models.job import Application, MatchResult
    from backend.models.user import User


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """The canonical, structured resume owned by a user."""

    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ResumeStatus] = mapped_column(
        enum_column(ResumeStatus),
        default=ResumeStatus.DRAFT,
        server_default=ResumeStatus.DRAFT.value,
        nullable=False,
    )
    content: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    latest_parse_status: Mapped[ParseStatus | None] = mapped_column(
        enum_column(ParseStatus),
        nullable=True,
    )
    last_parsed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    resume_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resume_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = orm_relationship(back_populates="resumes")
    resume_template: Mapped[ResumeTemplate | None] = orm_relationship(
        back_populates="resumes",
    )
    uploaded_resumes: Mapped[list[UploadedResume]] = orm_relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
    )
    generated_resumes: Mapped[list[GeneratedResume]] = orm_relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
    )
    resume_drafts: Mapped[list[ResumeDraft]] = orm_relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
    )
    match_results: Mapped[list[MatchResult]] = orm_relationship(back_populates="resume")
    applications: Mapped[list[Application]] = orm_relationship(back_populates="resume")
    career_insights: Mapped[list[CareerInsight]] = orm_relationship(
        back_populates="resume",
    )


class UploadedResume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A file the user uploaded for parsing."""

    __tablename__ = "uploaded_resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_status: Mapped[ParseStatus] = mapped_column(
        enum_column(ParseStatus),
        default=ParseStatus.PENDING,
        server_default=ParseStatus.PENDING.value,
        nullable=False,
    )
    parsed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    user: Mapped[User] = orm_relationship(back_populates="uploaded_resumes")
    resume: Mapped[Resume | None] = orm_relationship(back_populates="uploaded_resumes")


class ResumeTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reusable layout template for rendering resumes."""

    __tablename__ = "resume_templates"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_data: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )

    resumes: Mapped[list[Resume]] = orm_relationship(back_populates="resume_template")


class GeneratedResume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A resume generated by the AI service from a canonical resume."""

    __tablename__ = "generated_resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    generation_status: Mapped[GenerationStatus] = mapped_column(
        enum_column(GenerationStatus),
        default=GenerationStatus.PENDING,
        server_default=GenerationStatus.PENDING.value,
        nullable=False,
    )
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    user: Mapped[User] = orm_relationship(back_populates="generated_resumes")
    resume: Mapped[Resume | None] = orm_relationship(back_populates="generated_resumes")


class ResumeDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An in-progress resume being edited by the user."""

    __tablename__ = "resume_drafts"

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
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    draft_status: Mapped[DraftStatus] = mapped_column(
        enum_column(DraftStatus),
        default=DraftStatus.IN_PROGRESS,
        server_default=DraftStatus.IN_PROGRESS.value,
        nullable=False,
    )
    content: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )

    user: Mapped[User] = orm_relationship(back_populates="resume_drafts")
    resume: Mapped[Resume | None] = orm_relationship(back_populates="resume_drafts")
