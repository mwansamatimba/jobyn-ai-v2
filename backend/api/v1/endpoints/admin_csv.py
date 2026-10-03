"""Administrator-only CSV preview, import, and isolated batch history."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from backend.api.deps import require_admin
from backend.core.config import get_settings
from backend.ingestion.config import C5ConfigurationError, C5ExperimentConfig
from backend.ingestion.csv_import import (
    CsvVacancyImportError,
    CsvVacancyPersistenceError,
    csv_import_detail,
    csv_import_history,
    import_csv_vacancies,
    preview_csv_vacancies,
)
from backend.ingestion.store import C5ExperimentStore
from backend.models.user import User
from backend.schemas.admin_csv import (
    CsvImportDetail,
    CsvImportHistoryItem,
    CsvImportPreviewRecord,
    CsvImportPreviewResponse,
    CsvImportResultResponse,
    CsvImportRowError,
)

router = APIRouter(prefix="/admin/jobs/csv", tags=["Admin CSV Import"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_ROWS = 500
ALLOWED_MEDIA_TYPES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
}
PREVIEW_TOKEN_LIFETIME_SECONDS = 30 * 60


def _preview_fingerprint(
    content: bytes,
    mapping: dict[str, str] | None,
    experiment_id: str,
    database_path: Path,
    filename: str,
) -> str:
    canonical_mapping = json.dumps(mapping or {}, sort_keys=True, separators=(",", ":"))
    fingerprint = hmac.new(
        get_settings().SECRET_KEY.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    fingerprint.update(experiment_id.encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(content)
    fingerprint.update(b"\0")
    fingerprint.update(canonical_mapping.encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(str(database_path).encode("utf-8"))
    fingerprint.update(b"\0")
    fingerprint.update(filename.encode("utf-8"))
    return fingerprint.hexdigest()


def _confirmation_token(
    content: bytes,
    mapping: dict[str, str] | None,
    experiment_id: str,
    database_path: Path,
    filename: str,
) -> str:
    payload = {
        "expires": int(time.time()) + PREVIEW_TOKEN_LIFETIME_SECONDS,
        "fingerprint": _preview_fingerprint(
            content,
            mapping,
            experiment_id,
            database_path,
            filename,
        ),
        "nonce": secrets.token_hex(16),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        get_settings().SECRET_KEY.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{signed}"


def _verify_confirmation_token(
    token: str,
    content: bytes,
    mapping: dict[str, str] | None,
    experiment_id: str,
    database_path: Path,
    filename: str,
) -> None:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = hmac.new(
            get_settings().SECRET_KEY.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        padding = "=" * (-len(supplied_signature) % 4)
        signature = base64.urlsafe_b64decode(supplied_signature + padding)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
        payload_padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + payload_padding))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("expires"), int)
            or payload["expires"] <= int(time.time())
            or not hmac.compare_digest(
                str(payload.get("fingerprint", "")),
                _preview_fingerprint(
                    content,
                    mapping,
                    experiment_id,
                    database_path,
                    filename,
                ),
            )
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Preview expired or does not match this file and column mapping.",
        ) from None


def _isolated_config() -> C5ExperimentConfig:
    try:
        config = C5ExperimentConfig.from_environment()
    except C5ConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Isolated CSV import storage is not configured safely.",
        ) from None
    config = replace(config, retrieval_method="csv_import")
    database_path = _required_database_path(config)

    app_database = get_settings().DATABASE_URL
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if app_database.startswith(prefix):
            app_path = Path(app_database[len(prefix) :]).resolve()
            if app_path == database_path:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="CSV staging storage must be separate from application storage.",
                )
            break
    return config


def _required_database_path(config: C5ExperimentConfig) -> Path:
    database_path = config.sqlite_path
    if database_path is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Isolated CSV import storage is not configured safely.",
        )
    return database_path


def _isolated_config_and_store(
    *, readonly: bool, config: C5ExperimentConfig | None = None
) -> tuple[C5ExperimentConfig, C5ExperimentStore]:
    config = config or _isolated_config()
    database_path = _required_database_path(config)
    store = (
        C5ExperimentStore.open_readonly(database_path)
        if readonly
        else C5ExperimentStore(database_path)
    )
    return config, store


async def _read_upload(upload: UploadFile) -> tuple[str, bytes]:
    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload must be a CSV file with a .csv filename.",
        )
    media_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload content type must be CSV.",
        )
    settings = get_settings()
    maximum = min(settings.CSV_IMPORT_MAX_FILE_SIZE_BYTES, MAX_UPLOAD_BYTES)
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > maximum:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV exceeds the 5 MiB upload limit.",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file is empty.",
        )
    safe_filename = "".join(
        character for character in filename if character.isprintable()
    )[:255]
    return safe_filename, b"".join(chunks)


def _column_mapping(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    try:
        mapping = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Column mapping must be a JSON object.",
        ) from None
    if (
        not isinstance(mapping, dict)
        or not all(
            isinstance(key, str) and isinstance(target, str)
            for key, target in mapping.items()
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Column mapping must contain string header-to-field pairs.",
        )
    return mapping


def _preview_error(issue: Any) -> list[CsvImportRowError]:
    reason_fields = {
        "missing_required_fields": ("required_fields", "One or more required fields are missing."),
        "invalid_source_url": ("source_url", "Source URL must be an absolute HTTP(S) URL."),
        "invalid_application_url": (
            "application_url",
            "Application URL must be an absolute HTTP(S) URL.",
        ),
        "location_not_verified_as_zambian": (
            "location",
            "Location could not be verified as Zambia.",
        ),
        "invalid_date": ("posted_at/deadline", "Date value is invalid; use ISO-8601."),
        "source_not_allowed_for_isolated_import": (
            "source_name",
            "Source is not allowed for isolated CSV staging.",
        ),
        "duplicate_within_file": ("row", "Duplicate vacancy within this CSV; later row skipped."),
        "invalid_or_non_zambian_vacancy": (
            "row",
            "Vacancy failed the canonical validation rules.",
        ),
    }
    errors: list[CsvImportRowError] = []
    for reason in issue.reasons:
        field, message = reason_fields.get(
            reason,
            ("row", "Row could not be validated."),
        )
        errors.append(
            CsvImportRowError(row=issue.row_number, field=field, message=message)
        )
    return errors


def _map_import_error(error: CsvVacancyImportError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    )


@router.post(
    "/preview",
    response_model=CsvImportPreviewResponse,
    summary="Validate and preview a CSV without writing",
)
async def preview_csv(
    file: Annotated[UploadFile, File(...)],
    column_mapping: Annotated[str | None, Form()] = None,
    _: User = Depends(require_admin),
) -> CsvImportPreviewResponse:
    filename, content = await _read_upload(file)
    mapping = _column_mapping(column_mapping)
    config, store = _isolated_config_and_store(readonly=True)
    database_path = _required_database_path(config)
    try:
        preview = preview_csv_vacancies(
            content,
            config,
            store,
            column_mapping=mapping,
            max_file_size_bytes=min(
                get_settings().CSV_IMPORT_MAX_FILE_SIZE_BYTES, MAX_UPLOAD_BYTES
            ),
            max_rows=min(get_settings().CSV_IMPORT_MAX_ROWS, MAX_UPLOAD_ROWS),
        )
    except CsvVacancyImportError as error:
        raise _map_import_error(error) from None
    finally:
        store.close()

    within_duplicates = preview.duplicates_within_file
    invalid_count = len(preview.rejected_rows) - within_duplicates
    return CsvImportPreviewResponse(
        filename=filename,
        rows_detected=preview.total_rows,
        valid_rows=preview.eligible + within_duplicates,
        invalid_rows=invalid_count,
        duplicate_rows=within_duplicates + preview.duplicates_against_store,
        ready_to_import=preview.eligible,
        confirmation_token=_confirmation_token(
            content,
            mapping,
            config.experiment_id or "",
            database_path,
            filename,
        ),
        records=[
            CsvImportPreviewRecord(
                row=record.row_number,
                title=record.title,
                company=record.company,
                location=record.location,
                source_name=record.source_name,
                source_url_domain=record.source_url_domain,
                application_url_present=record.application_url_present,
                existing_action=record.existing_action,
            )
            for record in preview.records
        ],
        errors=[
            error
            for issue in preview.rejected_rows
            for error in _preview_error(issue)
        ],
        unmapped_columns=preview.unmapped_columns,
    )


@router.post(
    "/import",
    response_model=CsvImportResultResponse,
    summary="Confirm and import a CSV into isolated staging",
)
async def import_csv(
    file: Annotated[UploadFile, File(...)],
    confirmation_token: Annotated[str, Form(...)],
    column_mapping: Annotated[str | None, Form()] = None,
    _: User = Depends(require_admin),
) -> CsvImportResultResponse:
    filename, content = await _read_upload(file)
    mapping = _column_mapping(column_mapping)
    config = _isolated_config()
    database_path = _required_database_path(config)
    _verify_confirmation_token(
        confirmation_token,
        content,
        mapping,
        config.experiment_id or "",
        database_path,
        filename,
    )
    config, store = _isolated_config_and_store(readonly=False, config=config)
    try:
        result = import_csv_vacancies(
            content,
            config,
            store,
            column_mapping=mapping,
            max_file_size_bytes=min(
                get_settings().CSV_IMPORT_MAX_FILE_SIZE_BYTES, MAX_UPLOAD_BYTES
            ),
            max_rows=min(get_settings().CSV_IMPORT_MAX_ROWS, MAX_UPLOAD_ROWS),
            filename=filename,
            confirmation_id=hashlib.sha256(
                confirmation_token.encode("utf-8")
            ).hexdigest(),
        )
    except CsvVacancyImportError as error:
        raise _map_import_error(error) from None
    except CsvVacancyPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CSV import failed; the batch was rolled back.",
        ) from None
    finally:
        store.close()
    return CsvImportResultResponse(
        batch_id=result.batch_id,
        filename=filename,
        rows_submitted=result.total_rows,
        rows_valid=result.total_rows - result.rejected,
        jobs_created=result.accepted,
        jobs_updated=result.updated,
        duplicates_skipped=result.duplicates,
        rows_rejected=result.rejected,
        status="completed_with_rejections" if result.rejected else "completed",
        created_at=result.created_at,
    )


@router.get("/imports", response_model=list[CsvImportHistoryItem])
async def list_csv_imports(
    _: User = Depends(require_admin),
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[CsvImportHistoryItem]:
    config, store = _isolated_config_and_store(readonly=True)
    try:
        rows = csv_import_history(
            store,
            config.experiment_id or "",
            limit=limit,
            offset=offset,
        )
    finally:
        store.close()
    return [
        CsvImportHistoryItem(
            batch_id=row["batch_id"],
            created_at=row["created_at"],
            filename=row.get("filename") or "",
            row_count=row["total_rows"],
            valid_count=row.get("valid_count", row["total_rows"] - row["rejected"]),
            imported_count=row["accepted"],
            updated_count=row["updated"],
            duplicate_count=row["duplicates"],
            rejected_count=row["rejected"],
            status="completed_with_rejections" if row["rejected"] else "completed",
        )
        for row in rows
    ]


@router.get("/imports/{batch_id}", response_model=CsvImportDetail)
async def get_csv_import(
    batch_id: str,
    _: User = Depends(require_admin),
) -> CsvImportDetail:
    config, store = _isolated_config_and_store(readonly=True)
    try:
        batch = csv_import_detail(
            store,
            config.experiment_id or "",
            batch_id,
        )
    finally:
        store.close()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found.")
    return CsvImportDetail(
        batch_id=batch["batch_id"],
        created_at=batch["created_at"],
        filename=batch.get("filename") or "",
        row_count=batch["total_rows"],
        valid_count=batch.get("valid_count", batch["total_rows"] - batch["rejected"]),
        imported_count=batch["accepted"],
        updated_count=batch["updated"],
        duplicate_count=batch["duplicates"],
        rejected_count=batch["rejected"],
        status="completed_with_rejections" if batch["rejected"] else "completed",
        sources=batch["sources"],
        errors_retained=batch["errors_retained"],
    )
