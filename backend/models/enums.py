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


# ---------------------------------------------------------------------------
# Job ingestion enums
# ---------------------------------------------------------------------------


class IngestionStatus(StrEnum):
    """Lifecycle of an ingested job posting.

    active          — visible, verified, accepting applications
    possibly_closed — disappeared from one complete successful sync
    closed          — disappeared from two consecutive complete syncs,
                      or explicitly closed by source
    expired         — deadline has passed
    removed         — source owner requested takedown
    error           — persistent fetch/parse error, needs manual review
    """

    ACTIVE = "active"
    POSSIBLY_CLOSED = "possibly_closed"
    CLOSED = "closed"
    EXPIRED = "expired"
    REMOVED = "removed"
    ERROR = "error"


class RemoteEligibility(StrEnum):
    """Zambia-aware remote work eligibility classification."""

    NOT_REMOTE = "not_remote"
    ZAMBIA_ELIGIBLE = "zambia_eligible"
    AFRICA_ELIGIBLE = "africa_eligible"
    GLOBAL = "global"
    RESTRICTIONS_UNCLEAR = "restrictions_unclear"


class JobCategory(StrEnum):
    """Deterministic job category (keyword-based, no LLM)."""

    ACCOUNTING_FINANCE = "accounting_finance"
    ADMINISTRATION = "administration"
    AGRICULTURE = "agriculture"
    BUSINESS_MANAGEMENT = "business_management"
    CUSTOMER_SERVICE = "customer_service"
    EDUCATION = "education"
    ENGINEERING = "engineering"
    HEALTHCARE = "healthcare"
    HUMAN_RESOURCES = "human_resources"
    ICT_TECHNOLOGY = "ict_technology"
    LEGAL = "legal"
    MARKETING_COMMUNICATIONS = "marketing_communications"
    NGO_DEVELOPMENT = "ngo_development"
    PROJECT_MANAGEMENT = "project_management"
    RESEARCH = "research"
    SALES = "sales"
    SECURITY = "security"
    SKILLED_TRADES = "skilled_trades"
    INTERNSHIPS_GRADUATE = "internships_graduate"
    OTHER = "other"


class SourceType(StrEnum):
    """How a job source delivers its data."""

    API = "api"
    RSS = "rss"
    JSON = "json"
    XML = "xml"
    CSV = "csv"
    HTML = "html"
    PDF = "pdf"
    MANUAL = "manual"
    ATS = "ats"


class PermissionStatus(StrEnum):
    """Legal/compliance status of an ingestion source.

    A public URL is NOT automatic permission to republish content.
    """

    OFFICIAL_API = "official_api"       # Official public API — no key required
    OFFICIAL_FEED = "official_feed"     # Official public RSS/JSON feed
    PERMISSION_GRANTED = "permission_granted"   # Explicit written permission
    PERMISSION_REQUIRED = "permission_required" # Unknown/pending — do not ingest
    TERMS_UNCLEAR = "terms_unclear"             # ToS does not clearly permit
    DO_NOT_INGEST = "do_not_ingest"             # Explicitly prohibited


class ComplianceEventType(StrEnum):
    """Types of compliance events recorded against a source or job."""

    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REVOKED = "permission_revoked"
    TAKEDOWN_REQUESTED = "takedown_requested"
    TAKEDOWN_COMPLETED = "takedown_completed"
    TERMS_CHANGED = "terms_changed"
    SOURCE_DISABLED = "source_disabled"
    JOB_REMOVED = "job_removed"
    MANUAL_REVIEW = "manual_review"


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
