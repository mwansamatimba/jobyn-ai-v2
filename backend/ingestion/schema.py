"""NormalizedJob — the single output contract for every source connector.

Every connector must return a list of NormalizedJob instances.  The sync
orchestrator maps these directly onto the Job ORM model.  The frontend must
never depend on source-specific raw fields — only NormalizedJob fields are
persisted.

Design rules
------------
* All fields are optional except ``title``, ``company``, and
  ``application_url`` (the minimum required to display a meaningful card).
* Salary and deadline are NEVER invented — they remain None if the source
  does not provide them.
* ``application_url`` MUST be the real URL from the source.  Never fabricate.
* ``source_name`` is a short machine identifier, e.g. ``"greenhouse"``.
* ``external_id`` is the raw ID string as returned by the source.
* HTML in ``description`` and ``requirements`` is sanitized by the
  :func:`backend.ingestion.sanitize.sanitize_html` helper before storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NormalizedJob:
    """Connector output.  Maps onto the Job ORM model fields."""

    # Required
    title: str
    company: str
    application_url: str

    # Source identity
    source_name: str = ""
    external_id: str = ""
    source_url: str = ""
    attribution: str = ""

    # Location (classifiers fill in province / remote_eligibility)
    location: str = ""
    country: str = ""
    province: str = ""
    city: str = ""
    remote_eligibility: str = ""   # RemoteEligibility enum value

    # Job details
    description: str = ""
    requirements: str = ""
    employment_type: str = ""      # EmploymentType enum value
    experience_level: str = ""     # ExperienceLevel enum value
    category: str = ""             # JobCategory enum value
    department: str = ""
    seniority: str = ""

    # Dates — never invented
    date_posted: datetime | None = None
    deadline: datetime | None = None

    # Salary — never invented
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "USD"

    # Extra raw data kept for debugging / future use (not persisted to Job)
    raw: dict[str, Any] = field(default_factory=dict)
