"""Schemas for the administrator CSV job import workflow."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CSVRowError(BaseModel):
    row: int
    errors: list[str]


class JobImportPreview(BaseModel):
    filename: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicates: int
    ready_to_import: int
    errors: list[CSVRowError] = Field(default_factory=list)


class JobImportResult(BaseModel):
    filename: str
    rows_processed: int
    rows_imported: int
    rows_skipped_duplicates: int
    rows_rejected: int
    rows_failed: int
    errors: list[CSVRowError] = Field(default_factory=list)


class JobImportHistoryItem(BaseModel):
    id: str
    administrator: str | None
    filename: str | None
    timestamp: str
    total_rows: int
    imported_rows: int
    duplicate_rows: int
    rejected_rows: int
    failed_rows: int
    status: str
