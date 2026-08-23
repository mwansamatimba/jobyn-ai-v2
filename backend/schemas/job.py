"""Pydantic schemas for job and match-result endpoints.

Follows the same conventions as ``backend/schemas/resume.py``:
- All read schemas inherit ``ORMModel`` (``from_attributes=True`` via base).
- Write/input schemas use plain ``BaseModel``.
- ``PaginatedResponse`` from ``schemas/common.py`` wraps list endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import ORMModel, PaginatedResponse


# ------------------------------------------------------------------ #
# Job schemas                                                          #
# ------------------------------------------------------------------ #


class JobCreate(BaseModel):
    """Payload for creating an internal job posting."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    company_name: str = Field(min_length=1, max_length=255)
    company_logo_url: str | None = Field(default=None, max_length=512)
    location: str | None = Field(default=None, max_length=255)
    location_type: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = Field(default="USD", max_length=3)
    external_url: str | None = Field(default=None, max_length=512)


class JobRead(ORMModel):
    """Public representation of a job posting."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    company_name: str
    company_logo_url: str | None
    location: str | None
    location_type: str | None
    employment_type: str | None
    experience_level: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str
    is_active: bool
    source: str
    external_url: str | None
    created_at: datetime


# ------------------------------------------------------------------ #
# Match result schemas                                                 #
# ------------------------------------------------------------------ #


class MatchResultRead(ORMModel):
    """A single AI-computed match between a resume and a job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    resume_id: uuid.UUID | None
    job_id: uuid.UUID
    match_score: Decimal | None
    matched_skills: list[str] | None
    missing_skills: list[str] | None
    strengths: list[str] | None
    weaknesses: list[str] | None
    summary: str | None
    status: str | None
    matcher_type: str
    created_at: datetime


# ------------------------------------------------------------------ #
# Job match response                                                   #
# ------------------------------------------------------------------ #


class JobMatchItem(BaseModel):
    """One ranked match entry in the AI response, enriched with DB ids."""

    match_result_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    company: str
    match_score: int
    matching_skills: list[str]
    missing_skills: list[str]
    reason: str


class JobMatchResponse(BaseModel):
    """The full response returned by POST /jobs/match."""

    resume_id: uuid.UUID
    top_matches: list[JobMatchItem]
    overall_match_summary: str
    recommended_next_actions: list[str]


# ------------------------------------------------------------------ #
# Paginated wrappers                                                   #
# ------------------------------------------------------------------ #

JobListResponse = PaginatedResponse[JobRead]
MatchListResponse = PaginatedResponse[MatchResultRead]


# ------------------------------------------------------------------ #
# Deterministic match schemas                                          #
# ------------------------------------------------------------------ #


class DeterministicMatchItem(BaseModel):
    """One ranked match entry from the deterministic matching engine."""

    job_id: uuid.UUID
    job_title: str
    company: str
    location: str | None
    employment_type: str | None
    match_score: int
    match_level: str
    matched_skills: list[str]
    missing_skills: list[str]
    experience_match: bool
    role_match: bool
    recommendation: str
    skill_score: int
    experience_score: int
    role_score: int


class DeterministicMatchResponse(BaseModel):
    """Response for POST /jobs/deterministic-match."""

    resume_id: uuid.UUID
    total_jobs_evaluated: int
    matches: list[DeterministicMatchItem]
