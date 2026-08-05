"""User profile child models.

These records compose a candidate's profile and are deleted together with the
owning :class:`~backend.models.user.User`. The module-level ``orm_relationship``
alias avoids the name clash with the ``relationship`` column on
:class:`UserReference`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from backend.database.base_class import Base
from backend.models.enums import ProfileVisibility, UserSkillProficiency, enum_column
from backend.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.models.user import User


class UserSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-to-one preferences and configuration for a user account."""

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    preferred_language: Mapped[str] = mapped_column(
        String(10),
        default="en",
        server_default="en",
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="UTC",
        server_default="UTC",
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        server_default="USD",
        nullable=False,
    )
    profile_visibility: Mapped[ProfileVisibility] = mapped_column(
        enum_column(ProfileVisibility),
        default=ProfileVisibility.PRIVATE,
        server_default=ProfileVisibility.PRIVATE.value,
        nullable=False,
    )
    notification_preferences: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    user: Mapped[User] = orm_relationship(back_populates="settings")


class UserSkill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A skill a candidate declares on their profile."""

    __tablename__ = "user_skills"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    proficiency_level: Mapped[UserSkillProficiency | None] = mapped_column(
        enum_column(UserSkillProficiency),
        nullable=True,
    )
    years_of_experience: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1),
        nullable=True,
    )

    user: Mapped[User] = orm_relationship(back_populates="skills")


class UserExperience(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A work history entry on a candidate's profile."""

    __tablename__ = "user_experiences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = orm_relationship(back_populates="experiences")


class UserEducation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An educational qualification on a candidate's profile."""

    __tablename__ = "user_educations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    degree: Mapped[str | None] = mapped_column(String(255), nullable=True)
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    field_of_study: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    gpa: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = orm_relationship(back_populates="education")


class UserReference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A professional reference listed on a candidate's profile."""

    __tablename__ = "user_references"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship: Mapped[str] = mapped_column(String(100), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    user: Mapped[User] = orm_relationship(back_populates="references")
