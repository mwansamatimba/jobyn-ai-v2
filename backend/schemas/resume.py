"""Pydantic schemas for resume upload and candidate profile endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import ConfigDict

from backend.schemas.common import ORMModel


class UploadedResumeRead(ORMModel):
    """Public representation of a file upload record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    original_filename: str
    content_type: str | None
    file_size_bytes: int | None
    parse_status: str
    created_at: datetime


class ParsedResumeRead(ORMModel):
    """Upload record enriched with its parsed content."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    original_filename: str
    content_type: str | None
    file_size_bytes: int | None
    parse_status: str
    parsed_data: dict[str, Any] | None
    created_at: datetime


class CandidateProfileRead(ORMModel):
    """Structured candidate profile derived from an uploaded resume."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    status: str
    content: dict[str, Any]
    latest_parse_status: str | None
    created_at: datetime
