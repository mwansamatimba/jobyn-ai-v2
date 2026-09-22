"""Secure CSV job import service for administrators.

The importer deliberately reuses the existing ingestion NormalizedJob contract,
deduplication helpers, and ingestion persistence path. It does not introduce a
second jobs table or a second lifecycle.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ingestion.dedup import make_canonical_key, make_url_key
from backend.ingestion.sanitize import sanitize_html, validate_application_url
from backend.ingestion.schema import NormalizedJob
from backend.ingestion.sync import _process_job
from backend.models.enums import (
    ComplianceEventType,
    EmploymentType,
    ExperienceLevel,
    JobCategory,
    JobSource,
    PermissionStatus,
    RemoteEligibility,
    SourceType,
)
from backend.models.ingestion import ComplianceEvent, IngestionSource, JobIngestionSource
from backend.models.job import Job
from backend.models.user import User
from backend.schemas.admin_jobs import (
    CSVRowError,
    JobImportHistoryItem,
    JobImportPreview,
    JobImportResult,
)

ADMIN_CSV_SOURCE = "admin_csv"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1000

SUPPORTED_COLUMNS = (
    "title",
    "company",
    "company_name",
    "description",
    "requirements",
    "location",
    "province",
    "country",
    "employment_type",
    "experience_level",
    "category",
    "remote_eligibility",
    "date_posted",
    "posted_at",
    "deadline",
    "external_url",
    "application_url",
    "source",
    "external_id",
    "source_url",
    "attribution",
)

_ALIASES = {
    "company_name": "company",
    "application_url": "external_url",
    "posted_at": "date_posted",
}

_REQUIRED_COLUMNS = {"title", "company", "external_url"}


@dataclass
class ParsedCSV:
    filename: str
    rows: list[NormalizedJob]
    errors: list[CSVRowError]
    total_rows: int


class CSVImportError(ValueError):
    """Raised for an invalid CSV upload."""


class CSVImportService:
    """Validate, preview, and import bounded administrator CSV uploads."""

    async def read_upload(self, upload: UploadFile) -> bytes:
        filename = (upload.filename or "").strip()
        if not filename.lower().endswith(".csv"):
            raise CSVImportError("Only .csv files are accepted.")

        data = await upload.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise CSVImportError("CSV file exceeds the 5 MiB size limit.")
        if not data:
            raise CSVImportError("CSV file is empty.")
        return data

    def parse(self, data: bytes, filename: str) -> ParsedCSV:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CSVImportError("CSV must be valid UTF-8.") from exc

        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            raw_headers = reader.fieldnames
            if not raw_headers:
                raise CSVImportError("CSV must contain a header row.")

            headers = {
                _canonical_column(header)
                for header in raw_headers
                if header is not None and str(header).strip()
            }
            missing = sorted(_REQUIRED_COLUMNS - headers)
            if missing:
                raise CSVImportError(
                    "Missing required columns: " + ", ".join(missing) + "."
                )

            parsed: list[NormalizedJob] = []
            errors: list[CSVRowError] = []
            total_rows = 0

            for row_number, raw_row in enumerate(reader, start=2):
                total_rows += 1
                if total_rows > MAX_ROWS:
                    raise CSVImportError(f"CSV exceeds the {MAX_ROWS}-row limit.")

                normalized_row = _normalize_row(raw_row)
                if not any(value.strip() for value in normalized_row.values()):
                    errors.append(CSVRowError(row=row_number, errors=["Blank row."]))
                    continue

                try:
                    parsed.append(_row_to_job(normalized_row))
                except ValueError as exc:
                    errors.append(CSVRowError(row=row_number, errors=[str(exc)]))

            return ParsedCSV(
                filename=filename,
                rows=parsed,
                errors=errors,
                total_rows=total_rows,
            )
        except csv.Error as exc:
            raise CSVImportError(f"Malformed CSV: {exc}") from exc

    async def preview(
        self,
        session: AsyncSession,
        parsed: ParsedCSV,
    ) -> JobImportPreview:
        source = await self._ensure_admin_source(session)
        duplicate_indexes = await self._find_duplicate_indexes(session, source, parsed.rows)

        duplicate_rows = len(duplicate_indexes)
        valid_rows = len(parsed.rows)
        invalid_rows = len(parsed.errors)
        ready = max(valid_rows - duplicate_rows, 0)

        return JobImportPreview(
            filename=parsed.filename,
            total_rows=parsed.total_rows,
            valid_rows=valid_rows,
            invalid_rows=invalid_rows,
            duplicates=duplicate_rows,
            ready_to_import=ready,
            errors=parsed.errors,
        )

    async def import_rows(
        self,
        session: AsyncSession,
        parsed: ParsedCSV,
        administrator: User,
    ) -> JobImportResult:
        source = await self._ensure_admin_source(session)
        duplicate_indexes = await self._find_duplicate_indexes(session, source, parsed.rows)

        imported = 0
        duplicates = len(duplicate_indexes)
        rejected = len(parsed.errors)
        failed = 0
        errors = list(parsed.errors)

        # Everything below is one transaction. A fatal database error rolls
        # the whole import back instead of leaving a partial batch.
        try:
            seen_keys: set[str] = set()
            seen_urls: set[str] = set()

            for index, norm in enumerate(parsed.rows):
                if index in duplicate_indexes:
                    continue

                canonical_key = make_canonical_key(
                    source_name=ADMIN_CSV_SOURCE,
                    external_id=norm.external_id,
                    application_url=norm.application_url,
                    company=norm.company,
                    title=norm.title,
                    location=norm.location,
                )
                url_key = make_url_key(norm.application_url)

                # Protect against duplicate rows within the same upload.
                if canonical_key in seen_keys or url_key in seen_urls:
                    duplicates += 1
                    continue

                seen_keys.add(canonical_key)
                seen_urls.add(url_key)

                action = await _process_job(
                    session,
                    norm,
                    db_source=source,
                    seen_external_ids=set(),
                )

                if action == "created":
                    imported += 1
                    result = await session.execute(
                        select(Job).where(Job.canonical_key == canonical_key).limit(1)
                    )
                    job = result.scalar_one_or_none()
                    if job is not None:
                        job.created_by_user_id = administrator.id
                        _apply_explicit_csv_fields(job, norm)
                        await session.flush()
                elif action in {"duplicate", "updated"}:
                    duplicates += 1
                else:
                    rejected += 1

            event = ComplianceEvent(
                id=uuid.uuid4(),
                source_id=source.id,
                event_type=ComplianceEventType.MANUAL_REVIEW,
                requester=administrator.email,
                notes=json.dumps(
                    {
                        "kind": "admin_csv_import",
                        "filename": parsed.filename,
                        "total_rows": parsed.total_rows,
                        "imported_rows": imported,
                        "duplicate_rows": duplicates,
                        "rejected_rows": rejected,
                        "failed_rows": failed,
                        "status": "completed",
                    },
                    sort_keys=True,
                ),
            )
            session.add(event)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        return JobImportResult(
            filename=parsed.filename,
            rows_processed=parsed.total_rows,
            rows_imported=imported,
            rows_skipped_duplicates=duplicates,
            rows_rejected=rejected,
            rows_failed=failed,
            errors=errors,
        )

    async def history(
        self,
        session: AsyncSession,
        limit: int = 50,
    ) -> list[JobImportHistoryItem]:
        source = await self._ensure_admin_source(session)
        result = await session.execute(
            select(ComplianceEvent)
            .where(
                ComplianceEvent.source_id == source.id,
                ComplianceEvent.event_type == ComplianceEventType.MANUAL_REVIEW,
            )
            .order_by(ComplianceEvent.created_at.desc())
            .limit(limit)
        )
        items: list[JobImportHistoryItem] = []
        for event in result.scalars().all():
            try:
                payload = json.loads(event.notes or "{}")
            except json.JSONDecodeError:
                payload = {}
            if payload.get("kind") != "admin_csv_import":
                continue
            items.append(
                JobImportHistoryItem(
                    id=str(event.id),
                    administrator=event.requester,
                    filename=payload.get("filename"),
                    timestamp=event.created_at.isoformat(),
                    total_rows=int(payload.get("total_rows", 0)),
                    imported_rows=int(payload.get("imported_rows", 0)),
                    duplicate_rows=int(payload.get("duplicate_rows", 0)),
                    rejected_rows=int(payload.get("rejected_rows", 0)),
                    failed_rows=int(payload.get("failed_rows", 0)),
                    status=str(payload.get("status", "completed")),
                )
            )
        return items

    async def _ensure_admin_source(self, session: AsyncSession) -> IngestionSource:
        result = await session.execute(
            select(IngestionSource).where(IngestionSource.name == ADMIN_CSV_SOURCE).limit(1)
        )
        source = result.scalar_one_or_none()
        if source is None:
            source = IngestionSource(
                id=uuid.uuid4(),
                name=ADMIN_CSV_SOURCE,
                organization_name="Jobyn AI administrators",
                source_type=SourceType.MANUAL,
                base_url=None,
                permission_status=PermissionStatus.PERMISSION_GRANTED,
                attribution_template="Imported manually by a Jobyn administrator",
                active=True,
            )
            session.add(source)
            await session.flush()
        elif not source.is_ingestion_permitted():
            source.permission_status = PermissionStatus.PERMISSION_GRANTED
            source.active = True
            await session.flush()
        return source

    async def _find_duplicate_indexes(
        self,
        session: AsyncSession,
        source: IngestionSource,
        jobs: list[NormalizedJob],
    ) -> set[int]:
        duplicate_indexes: set[int] = set()
        seen_keys: set[str] = set()
        seen_urls: set[str] = set()

        existing_ids = {
            row.external_id
            for row in (
                await session.execute(
                    select(JobIngestionSource.external_id).where(
                        JobIngestionSource.source_id == source.id,
                        JobIngestionSource.external_id.is_not(None),
                    )
                )
            ).all()
            if row.external_id
        }
        existing_urls = {
            make_url_key(row.application_url)
            for row in (
                await session.execute(
                    select(JobIngestionSource.application_url).where(
                        JobIngestionSource.source_id == source.id,
                        JobIngestionSource.application_url.is_not(None),
                    )
                )
            ).all()
            if row.application_url
        }

        for index, norm in enumerate(jobs):
            canonical_key = make_canonical_key(
                source_name=ADMIN_CSV_SOURCE,
                external_id=norm.external_id,
                application_url=norm.application_url,
                company=norm.company,
                title=norm.title,
                location=norm.location,
            )
            url_key = make_url_key(norm.application_url)

            if (
                (norm.external_id and norm.external_id in existing_ids)
                or canonical_key in seen_keys
                or url_key in existing_urls
                or url_key in seen_urls
            ):
                duplicate_indexes.add(index)
                continue

            result = await session.execute(
                select(Job.id).where(
                    Job.deleted_at.is_(None),
                    Job.canonical_key.in_([canonical_key, url_key]),
                ).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                duplicate_indexes.add(index)
                continue

            seen_keys.add(canonical_key)
            seen_urls.add(url_key)

        return duplicate_indexes


def _canonical_column(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_row(raw_row: dict[str | None, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw_row.items():
        if key is None:
            continue
        canonical = _ALIASES.get(_canonical_column(str(key)), _canonical_column(str(key)))
        normalized[canonical] = "" if value is None else str(value).strip()
    return normalized


def _row_to_job(row: dict[str, str]) -> NormalizedJob:
    title = row.get("title", "").strip()
    company = row.get("company", "").strip()
    external_url = validate_application_url(row.get("external_url"))
    if not title:
        raise ValueError("title is required.")
    if not company:
        raise ValueError("company is required.")
    if not external_url:
        raise ValueError("external_url/application_url must be a valid http(s) URL.")

    employment_type = _enum_value(EmploymentType, row.get("employment_type"), "employment_type")
    experience_level = _enum_value(
        ExperienceLevel, row.get("experience_level"), "experience_level"
    )
    category = _enum_value(JobCategory, row.get("category"), "category")
    remote_eligibility = _enum_value(
        RemoteEligibility, row.get("remote_eligibility"), "remote_eligibility"
    )
    source = _enum_value(JobSource, row.get("source"), "source") or JobSource.EXTERNAL.value

    location = row.get("location", "").strip()
    if not location and remote_eligibility:
        location = {
            RemoteEligibility.ZAMBIA_ELIGIBLE.value: "Remote - Zambia",
            RemoteEligibility.AFRICA_ELIGIBLE.value: "Remote - Africa",
            RemoteEligibility.GLOBAL.value: "Remote - Global",
            RemoteEligibility.RESTRICTIONS_UNCLEAR.value: "Remote",
            RemoteEligibility.NOT_REMOTE.value: "",
        }.get(remote_eligibility, "")

    date_posted = _parse_datetime(row.get("date_posted"), "date_posted")
    deadline = _parse_datetime(row.get("deadline"), "deadline")

    return NormalizedJob(
        title=title,
        company=company,
        application_url=external_url,
        source_name=ADMIN_CSV_SOURCE,
        external_id=row.get("external_id", "").strip(),
        source_url=row.get("source_url", "").strip() or external_url,
        attribution=row.get("attribution", "").strip(),
        location=location,
        country=row.get("country", "").strip(),
        province=row.get("province", "").strip(),
        description=sanitize_html(row.get("description")),
        requirements=sanitize_html(row.get("requirements")),
        employment_type=employment_type or "",
        experience_level=experience_level or "",
        category=category or "",
        remote_eligibility=remote_eligibility or "",
        date_posted=date_posted,
        deadline=deadline,
        raw={"source": source, "csv": row},
    )


def _enum_value(enum_type: type[Any], raw: str | None, field_name: str) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    values = {member.value: member.value for member in enum_type}
    values.update({member.name.lower(): member.value for member in enum_type})
    if value not in values:
        raise ValueError(f"invalid {field_name}: {raw!r}.")
    return values[value]


def _parse_datetime(raw: str | None, field_name: str) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: expected ISO-8601 date/datetime.") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _apply_explicit_csv_fields(job: Job, norm: NormalizedJob) -> None:
    if norm.country:
        job.country = norm.country
    if norm.province:
        job.province = norm.province
    if norm.category:
        job.category = JobCategory(norm.category)
    if norm.remote_eligibility:
        job.remote_eligibility = RemoteEligibility(norm.remote_eligibility)
    if norm.raw.get("source"):
        job.source = JobSource(norm.raw["source"])


def csv_template() -> str:
    headers = [
        "title",
        "company",
        "description",
        "requirements",
        "location",
        "province",
        "country",
        "employment_type",
        "experience_level",
        "category",
        "remote_eligibility",
        "date_posted",
        "deadline",
        "external_url",
        "source",
        "external_id",
        "source_url",
        "attribution",
    ]
    example = [
        "Example role (template only)",
        "Example company (template only)",
        "Replace with the vacancy description",
        "Replace with requirements",
        "Lusaka, Zambia",
        "Lusaka Province",
        "Zambia",
        "full_time",
        "mid",
        "other",
        "not_remote",
        "2026-09-22",
        "2026-10-15",
        "https://example.com/jobs/example",
        "external",
        "example-id",
        "https://example.com/jobs/example",
        "Imported source attribution",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerow(example)
    return buffer.getvalue()
