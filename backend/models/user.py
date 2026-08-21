"""User model and its relationships.

The ``email`` column carries a unique constraint (including soft-deleted rows)
so a previously deleted account's address cannot be silently reused by a new
signup.

Relationship targets are declared as forward references and resolved lazily by
the shared mapper registry; related classes are imported only for type checking,
which keeps the model module graph acyclic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base_class import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.career import CareerInsight
    from backend.models.job import Application, Job, MatchResult
    from backend.models.resume import GeneratedResume, Resume, ResumeDraft, UploadedResume
    from backend.models.user_profile import (
        UserEducation,
        UserExperience,
        UserReference,
        UserSettings,
        UserSkill,
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """An authenticated account on the Jobyn platform."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    # Profile composition: these records only exist within a user's profile.
    settings: Mapped[UserSettings | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    skills: Mapped[list[UserSkill]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    experiences: Mapped[list[UserExperience]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    education: Mapped[list[UserEducation]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    references: Mapped[list[UserReference]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # Resume domain: documents owned by the user.
    resumes: Mapped[list[Resume]] = relationship(back_populates="user")
    uploaded_resumes: Mapped[list[UploadedResume]] = relationship(
        back_populates="user",
    )
    generated_resumes: Mapped[list[GeneratedResume]] = relationship(
        back_populates="user",
    )
    resume_drafts: Mapped[list[ResumeDraft]] = relationship(
        back_populates="user",
    )

    # Job domain: matching and applications owned by the user.
    created_jobs: Mapped[list[Job]] = relationship(back_populates="created_by_user")
    match_results: Mapped[list[MatchResult]] = relationship(back_populates="user")
    applications: Mapped[list[Application]] = relationship(back_populates="user")

    # Career intelligence: persisted AI career analyses.
    career_insights: Mapped[list[CareerInsight]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @property
    def subject(self) -> str:
        """Stable token subject: the stringified primary key."""
        return str(self.id)
