"""Source-independent output contract for ingestion connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class NormalizedJob:
    """A connector result that contains no database or source-specific behavior."""

    title: str
    company: str
    application_url: str
    source_name: str
    external_id: str = ""
    source_url: str = ""
    attribution: str = ""
    location: str = ""
    country: str = ""
    province: str = ""
    city: str = ""
    remote_eligibility: str = ""
    description: str = ""
    requirements: str = ""
    category: str = ""
    posted_at: datetime | None = None
    deadline: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)
