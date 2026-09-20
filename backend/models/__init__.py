"""ORM model registration point.

Every new feature must register its ORM model here, for example:

.. code-block:: python

    from backend.models.user import User

Importing this package populates ``Base.metadata`` with the full schema before
Alembic or the application ever introspects it. Forgetting to register a model
here is the most common cause of silently missing tables in migrations, so this
package is intentionally the single aggregation point.

To add relationships between models, follow the conventions documented in the
README to avoid mapper errors:

* declare all ``Mapped[...]`` relationship targets as strings (lazy mapping)
* always pair ``relationship(back_populates=...)`` explicitly, never ``backref``
* set ``foreign_keys`` explicitly when a table has more than one FK to the same
  target
"""

from backend.models.career import CareerInsight
from backend.models.enums import (
    ApplicationStatus,
    ComplianceEventType,
    DraftStatus,
    EmploymentType,
    ExperienceLevel,
    GenerationStatus,
    IngestionStatus,
    JobCategory,
    JobSource,
    LocationType,
    ParseStatus,
    PermissionStatus,
    ProfileVisibility,
    RemoteEligibility,
    ResumeStatus,
    SourceType,
    UserSkillProficiency,
)
from backend.models.ingestion import ComplianceEvent, IngestionSource, JobIngestionSource, JobObservation
from backend.models.job import Application, Job, MatchResult
from backend.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from backend.models.resume import (
    GeneratedResume,
    Resume,
    ResumeDraft,
    ResumeTemplate,
    UploadedResume,
)
from backend.models.user import User
from backend.models.user_profile import (
    UserEducation,
    UserExperience,
    UserReference,
    UserSettings,
    UserSkill,
)

__all__ = [
    "Application",
    "ApplicationStatus",
    "CareerInsight",
    "ComplianceEvent",
    "ComplianceEventType",
    "DraftStatus",
    "EmploymentType",
    "ExperienceLevel",
    "GeneratedResume",
    "GenerationStatus",
    "IngestionSource",
    "IngestionStatus",
    "Job",
    "JobCategory",
    "JobIngestionSource",
    "JobObservation",
    "JobSource",
    "LocationType",
    "MatchResult",
    "ParseStatus",
    "PermissionStatus",
    "ProfileVisibility",
    "RemoteEligibility",
    "Resume",
    "ResumeDraft",
    "ResumeStatus",
    "ResumeTemplate",
    "SoftDeleteMixin",
    "SourceType",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "UploadedResume",
    "User",
    "UserEducation",
    "UserExperience",
    "UserReference",
    "UserSettings",
    "UserSkill",
    "UserSkillProficiency",
]
