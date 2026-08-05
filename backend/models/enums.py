"""Enumeration types for the Jobyn domain model.

Enums are stored as portable, non-native SQLAlchemy enums (``VARCHAR`` + a
``CHECK`` constraint on both PostgreSQL and SQLite) so migrations behave
identically across the production and development databases. Member *values*
are the stored representation; use the member names in application code, e.g.
``Application.status == ApplicationStatus.APPLIED``.
"""

import enum

from sqlalchemy import Enum


class StrEnum(enum.StrEnum):
    """String enum whose members compare and render as their values."""

    def __str__(self) -> str:
        return self.value


class ProfileVisibility(StrEnum):
    """Who may view a user's public profile."""

    PRIVATE = "private"
    PUBLIC = "public"
    CONTACTS_ONLY = "contacts_only"


class UserSkillProficiency(StrEnum):
    """Self-declared proficiency level for a skill."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class ParseStatus(StrEnum):
    """Lifecycle of a resume upload's AI parsing pipeline."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ResumeStatus(StrEnum):
    """Lifecycle of a canonical resume document."""

    DRAFT = "draft"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class DraftStatus(StrEnum):
    """Lifecycle of an in-progress resume draft."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class GenerationStatus(StrEnum):
    """Lifecycle of an AI-generated resume."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LocationType(StrEnum):
    """Where a job is performed."""

    REMOTE = "remote"
    ONSITE = "onsite"
    HYBRID = "hybrid"


class EmploymentType(StrEnum):
    """Employment arrangement offered by a job."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"


class ExperienceLevel(StrEnum):
    """Seniority band targeted by a job."""

    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    EXECUTIVE = "executive"


class JobSource(StrEnum):
    """Where a job posting originated."""

    INTERNAL = "internal"
    EXTERNAL = "external"


class ApplicationStatus(StrEnum):
    """Lifecycle of a job application."""

    DRAFT = "draft"
    APPLIED = "applied"
    UNDER_REVIEW = "under_review"
    INTERVIEWING = "interviewing"
    OFFERED = "offered"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


def enum_column(enum_class: type[StrEnum]) -> Enum:
    """Return a portable, non-native SQLAlchemy Enum column type for a StrEnum.

    The stored values are the enum member values (lowercase), which keeps the
    database rows readable while application code uses the members.
    """

    return Enum(
        enum_class,
        name=enum_class.__name__,
        native_enum=False,
        length=32,
        values_callable=lambda cls: [member.value for member in cls],
    )
