"""Pydantic schemas for the AI Application Copilot endpoint."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, ConfigDict


class ApplicationCopilotRequest(BaseModel):
    """Payload for POST /application-copilot/generate."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID = Field(
        description="UUID of an existing active job posting."
    )

    additional_context: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional factual context supplied by the candidate. "
            "Only information that is true and verifiable should be included."
        ),
    )


class MatchedRequirement(BaseModel):
    """A job requirement matched against evidence from the candidate."""

    requirement: str = Field(
        description="Requirement identified in the job description."
    )

    evidence: str = Field(
        description="Evidence from the candidate's resume or career context."
    )


class AddressedSkillGap(BaseModel):
    """A skill gap and how the candidate addresses or plans to address it."""

    gap: str = Field(
        description="Skill or capability that is missing or insufficiently demonstrated."
    )

    response: str = Field(
        description="How the candidate addresses the gap or plans to develop it."
    )


class ApplicationCopilotResponse(BaseModel):
    """Structured application package returned by the Application Copilot."""

    model_config = ConfigDict(extra="ignore")

    job_id: uuid.UUID
    job_title: str
    company: str

    # AI-generated application package
    cover_letter: str

    key_selling_points: list[str] = Field(
        default_factory=list
    )

    matched_requirements: list[MatchedRequirement] = Field(
        default_factory=list
    )

    addressed_skill_gaps: list[AddressedSkillGap] = Field(
        default_factory=list
    )

    application_tips: list[str] = Field(
        default_factory=list
    )