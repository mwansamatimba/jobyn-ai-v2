"""Pydantic schemas for the AI Application Copilot endpoint.

Conventions
-----------
- Request schema uses plain ``BaseModel``.
- Response schema uses plain ``BaseModel`` (not ORM-backed — generation is
  stateless; no table is persisted).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ApplicationCopilotRequest(BaseModel):
    """Payload for POST /application-copilot/generate."""

    job_id: uuid.UUID = Field(
        description="UUID of an existing active job posting to generate the package for."
    )
    additional_context: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional free-text notes from the candidate that the AI may "
            "incorporate — e.g. a relevant side project or specific motivation. "
            "Only factual, verifiable information should be included here."
        ),
    )


class ApplicationCopilotResponse(BaseModel):
    """Structured application package returned by POST /application-copilot/generate."""

    job_id: uuid.UUID
    job_title: str
    company: str

    # AI-generated fields
    cover_letter: str
    key_selling_points: list[str]
    matched_requirements: list[str]
    addressed_skill_gaps: list[str]
    application_tips: list[str]
