"""Pydantic schemas for the career navigator and skill gap analysis endpoints.

Conventions
-----------
- Request schemas use plain ``BaseModel`` (no ORM attributes needed).
- Response schemas inherit ``ORMModel`` which sets ``from_attributes=True``.
- ``CareerAnalysisResponse`` is not an ORM read — it wraps the full
  ``CareerInsight`` record with its analysis unpacked for client convenience.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import ORMModel, PaginatedResponse


# ------------------------------------------------------------------ #
# Request schema                                                       #
# ------------------------------------------------------------------ #


class CareerNavigatorRequest(BaseModel):
    """Payload for POST /career/analyze."""

    target_role: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Optional role to tailor the analysis towards, e.g. 'Staff Engineer'. "
            "When omitted the AI infers the best direction from the resume."
        ),
    )


# ------------------------------------------------------------------ #
# Career path step                                                     #
# ------------------------------------------------------------------ #


class CareerPathStage(BaseModel):
    """One stage in the AI-generated career path."""

    stage: str
    timeline: str
    actions: list[str]


# ------------------------------------------------------------------ #
# Insight read schema (ORM-backed)                                     #
# ------------------------------------------------------------------ #


class CareerInsightRead(ORMModel):
    """Stored career insight record — maps directly from the ORM model."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    resume_id: uuid.UUID | None
    target_role: str | None
    analysis: dict[str, Any]
    created_at: datetime


# ------------------------------------------------------------------ #
# Full analysis response (enriched, returned by POST /career/analyze) #
# ------------------------------------------------------------------ #


class CareerAnalysisResponse(BaseModel):
    """Structured career analysis returned to the client.

    Unpacks the ``analysis`` JSON from the persisted insight so the client
    receives a flat, well-typed response rather than an opaque blob.
    """

    insight_id: uuid.UUID
    resume_id: uuid.UUID | None
    target_role: str | None
    created_at: datetime

    # AI-generated fields from CareerNavigatorService output
    career_direction: str
    recommended_roles: list[str]
    career_path: list[CareerPathStage]
    skill_priorities: list[str]
    certification_recommendations: list[str]
    job_search_strategy: list[str]
    career_advice: str

    @classmethod
    def from_insight(cls, insight) -> "CareerAnalysisResponse":
        """Build the response from a :class:`CareerInsight` ORM instance.

        Args:
            insight: The persisted :class:`~backend.models.career.CareerInsight`.

        Returns:
            A populated :class:`CareerAnalysisResponse`.
        """
        a = insight.analysis or {}
        return cls(
            insight_id=insight.id,
            resume_id=insight.resume_id,
            target_role=insight.target_role,
            created_at=insight.created_at,
            career_direction=a.get("career_direction", ""),
            recommended_roles=a.get("recommended_roles", []),
            career_path=[
                CareerPathStage(
                    stage=s.get("stage", ""),
                    timeline=s.get("timeline", ""),
                    actions=s.get("actions", []),
                )
                for s in a.get("career_path", [])
                if isinstance(s, dict)
            ],
            skill_priorities=a.get("skill_priorities", []),
            certification_recommendations=a.get("certification_recommendations", []),
            job_search_strategy=a.get("job_search_strategy", []),
            career_advice=a.get("career_advice", ""),
        )


# ------------------------------------------------------------------ #
# Paginated wrapper                                                    #
# ------------------------------------------------------------------ #

CareerInsightListResponse = PaginatedResponse[CareerInsightRead]
