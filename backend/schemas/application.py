"""Pydantic schemas for the Application Tracking endpoints.

Conventions
-----------
- Request/input schemas use plain ``BaseModel``.
- Read schemas inherit ``ORMModel`` (``from_attributes=True`` via base).
- ``PaginatedResponse`` from ``schemas/common.py`` wraps the list endpoint.

Field names match the actual ``Application`` ORM model columns exactly.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import ORMModel, PaginatedResponse


# ------------------------------------------------------------------ #
# Request schemas                                                      #
# ------------------------------------------------------------------ #


class ApplicationCreate(BaseModel):
    """Payload for POST /applications — create a new application."""

    job_id: uuid.UUID = Field(description="UUID of the target job posting.")
    cover_letter: str | None = Field(
        default=None,
        description=(
            "Optional cover letter text. Can be sourced from the Application "
            "Copilot or written manually."
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Optional private notes about this application.",
    )
    status: str | None = Field(
        default=None,
        description=(
            "Initial status. Defaults to 'draft'. "
            "Valid values: draft, applied."
        ),
    )


class ApplicationUpdate(BaseModel):
    """Payload for PATCH /applications/{id} — update permitted fields.

    Only fields that users are allowed to change are exposed here.
    ``user_id`` and ``job_id`` are immutable after creation.
    """

    status: str | None = Field(default=None, description="New status value.")
    cover_letter: str | None = Field(default=None)
    notes: str | None = Field(default=None)
    applied_at: date | None = Field(
        default=None,
        description="Date the application was submitted externally.",
    )
    last_activity_at: date | None = Field(
        default=None,
        description="Date of the most recent activity on this application.",
    )


# ------------------------------------------------------------------ #
# Read schema                                                          #
# ------------------------------------------------------------------ #


class ApplicationRead(ORMModel):
    """Public representation of an Application record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    job_id: uuid.UUID
    resume_id: uuid.UUID | None
    status: str
    cover_letter: str | None
    applied_at: date | None
    last_activity_at: date | None
    notes: str | None
    resume_version: int | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ #
# Paginated wrapper                                                    #
# ------------------------------------------------------------------ #

ApplicationListResponse = PaginatedResponse[ApplicationRead]
