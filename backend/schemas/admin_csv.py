"""Response contracts for the administrator-only isolated CSV workflow."""

from pydantic import BaseModel


class CsvImportRowError(BaseModel):
    row: int
    field: str
    message: str


class CsvImportPreviewRecord(BaseModel):
    row: int
    title: str
    company: str
    location: str
    source_name: str
    source_url_domain: str
    application_url_present: bool
    existing_action: str


class CsvImportPreviewResponse(BaseModel):
    filename: str
    rows_detected: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    ready_to_import: int
    confirmation_token: str
    records: list[CsvImportPreviewRecord]
    errors: list[CsvImportRowError]
    unmapped_columns: list[str]


class CsvImportResultResponse(BaseModel):
    batch_id: str
    filename: str
    rows_submitted: int
    rows_valid: int
    jobs_created: int
    jobs_updated: int
    duplicates_skipped: int
    rows_rejected: int
    status: str
    created_at: str


class CsvImportHistoryItem(BaseModel):
    batch_id: str
    created_at: str
    filename: str
    row_count: int
    valid_count: int
    imported_count: int
    updated_count: int
    duplicate_count: int
    rejected_count: int
    status: str


class CsvImportDetail(CsvImportHistoryItem):
    sources: list[dict[str, object]]
    errors_retained: bool
