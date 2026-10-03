"""Offline CSV vacancy preview and import into the isolated C5 SQLite store."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.core.config import get_settings
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.processing import prepare_job, validate_url
from backend.ingestion.schema import NormalizedJob
from backend.ingestion.sources import SOURCE_REGISTRY, namespace_for
from backend.ingestion.store import C5ExperimentStore

REQUIRED_COLUMNS = frozenset(
    {
        "title",
        "company",
        "location",
        "description",
        "source_name",
        "source_url",
        "application_url",
    }
)
OPTIONAL_COLUMNS = frozenset(
    {
        "external_id",
        "country",
        "province",
        "city",
        "remote_eligibility",
        "requirements",
        "posted_at",
        "deadline",
        "category",
        "attribution",
    }
)
ALL_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS
ALIASES = {
    "title": ("job_title", "position", "role"),
    "company": ("company_name", "employer", "organization"),
    "location": ("job_location",),
    "description": ("job_description",),
    "source_name": ("source", "job_source"),
    "source_url": ("original_url", "listing_url", "job_url"),
    "application_url": ("apply_url", "apply_link", "application_link"),
    "external_id": ("job_id",),
    "province": ("state", "region"),
    "remote_eligibility": ("remote",),
    "requirements": ("qualifications",),
    "posted_at": ("date_posted", "published_at"),
    "deadline": ("application_deadline", "closing_date"),
    "category": ("job_category",),
    "attribution": ("source_attribution",),
}
_ALIAS_TO_COLUMN = {
    alias: column for column, aliases in ALIASES.items() for alias in aliases
}
_PERSONAL_HEADER = re.compile(
    r"(?:^|_)(?:candidate|applicant|resume|cv|profile|email|phone|password|"
    r"credential|token|cookie|user)(?:_|$)",
    re.IGNORECASE,
)


class CsvVacancyImportError(ValueError):
    """Raised for unsafe or malformed CSV import requests."""


class CsvVacancyPersistenceError(RuntimeError):
    """Raised when the CSV import transaction fails and has been rolled back."""


@dataclass(frozen=True, slots=True)
class CsvRowIssue:
    row_number: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CsvVacancyPreview:
    row_number: int
    title: str
    company: str
    location: str
    source_name: str
    source_url_domain: str
    application_url_present: bool
    existing_action: str


@dataclass(slots=True)
class CsvVacancyPreviewResult:
    total_rows: int = 0
    parsed_rows: int = 0
    missing_required_fields: int = 0
    invalid_urls: int = 0
    unverified_locations: int = 0
    duplicates_within_file: int = 0
    duplicates_against_store: int = 0
    invalid_dates: int = 0
    eligible: int = 0
    rejected_rows: list[CsvRowIssue] = field(default_factory=list)
    records: list[CsvVacancyPreview] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CsvVacancyImportResult:
    batch_id: str
    accepted: int
    updated: int
    duplicates: int
    skipped: int
    rejected: int
    total_rows: int
    created_at: str


@dataclass(frozen=True, slots=True)
class _ParsedRow:
    row_number: int
    job: NormalizedJob | None
    reasons: tuple[str, ...]
    canonical_identity: str | None
    source_namespace: str | None


def _normal_header(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[\s-]+", "_", value.strip().lstrip("\ufeff").lower())


def _column_mapping(
    headers: list[str], mapping: Mapping[str, str] | None
) -> tuple[dict[str, str], list[str]]:
    normalized_headers = [_normal_header(header) for header in headers]
    if len(set(normalized_headers)) != len(normalized_headers):
        raise CsvVacancyImportError("CSV contains duplicate column headers")
    for header in normalized_headers:
        if _PERSONAL_HEADER.search(header):
            raise CsvVacancyImportError(
                "CSV contains a prohibited personal-data column"
            )

    overrides = {
        _normal_header(key): value.strip().lower()
        for key, value in (mapping or {}).items()
    }
    invalid_targets = set(overrides.values()) - ALL_COLUMNS
    if invalid_targets:
        raise CsvVacancyImportError("column mapping contains an unsupported target")
    unknown_mapping_headers = set(overrides) - set(normalized_headers)
    if unknown_mapping_headers:
        raise CsvVacancyImportError("column mapping references an unknown CSV header")

    resolved: dict[str, str] = {}
    unmapped: list[str] = []
    for original, header in zip(headers, normalized_headers, strict=True):
        target = overrides.get(header)
        if target is None:
            target = header if header in ALL_COLUMNS else _ALIAS_TO_COLUMN.get(header)
        if target is None:
            unmapped.append(original)
            continue
        if target in resolved.values():
            raise CsvVacancyImportError(
                "multiple CSV columns map to the same vacancy field"
            )
        resolved[original] = target

    missing = REQUIRED_COLUMNS - set(resolved.values())
    if missing:
        raise CsvVacancyImportError(
            "CSV is missing required columns; map headers explicitly before import"
        )
    return resolved, unmapped


def _parse_date(value: str) -> datetime | None:
    if not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw)
        except ValueError as error:
            raise ValueError("invalid_date") from error
        parsed = datetime.combine(parsed_date, time.min, tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _source_policy(source_name: str) -> tuple[str, str]:
    policy = SOURCE_REGISTRY.get(source_name.strip().lower())
    if (
        policy is None
        or not policy.experiment_allowed
        or policy.active
        or policy.permission_status.value != "permission_required"
    ):
        raise ValueError("source_not_allowed_for_isolated_import")
    return policy.name, namespace_for(policy.name)


def _row_job(values: Mapping[str, str], row_number: int) -> _ParsedRow:
    reasons: list[str] = []
    normalized = {key: (values.get(key) or "").strip() for key in ALL_COLUMNS}
    if any(not normalized[column] for column in REQUIRED_COLUMNS):
        reasons.append("missing_required_fields")

    valid_urls: dict[str, str] = {}
    for column in ("source_url", "application_url"):
        validated = validate_url(normalized[column])
        if validated is None:
            reasons.append(f"invalid_{column}")
        else:
            valid_urls[column] = validated

    parsed_dates: dict[str, datetime | None] = {}
    for column in ("posted_at", "deadline"):
        try:
            parsed_dates[column] = _parse_date(normalized[column])
        except ValueError:
            reasons.append("invalid_date")
            parsed_dates[column] = None

    source_name = normalized["source_name"].lower()
    namespace: str | None = None
    try:
        source_name, namespace = _source_policy(source_name)
    except ValueError:
        reasons.append("source_not_allowed_for_isolated_import")

    if not reasons or "missing_required_fields" not in reasons:
        from backend.ingestion.engine import is_zambia_location

        if not is_zambia_location(normalized["location"], normalized["country"]):
            reasons.append("location_not_verified_as_zambian")

    if reasons:
        return _ParsedRow(row_number, None, tuple(dict.fromkeys(reasons)), None, namespace)

    job = NormalizedJob(
        title=normalized["title"],
        company=normalized["company"],
        application_url=valid_urls["application_url"],
        source_name=source_name,
        external_id=normalized["external_id"],
        source_url=valid_urls["source_url"],
        attribution=normalized["attribution"] or f"Source: {source_name}",
        location=normalized["location"],
        country=normalized["country"],
        province=normalized["province"],
        city=normalized["city"],
        remote_eligibility=normalized["remote_eligibility"],
        description=normalized["description"],
        requirements=normalized["requirements"],
        category=normalized["category"],
        posted_at=parsed_dates["posted_at"],
        deadline=parsed_dates["deadline"],
        raw={key: value for key, value in normalized.items() if value},
    )
    prepared = prepare_job(job)
    if prepared is None:
        return _ParsedRow(
            row_number,
            None,
            ("invalid_or_non_zambian_vacancy",),
            None,
            namespace,
        )
    prepared_job, identity = prepared
    return _ParsedRow(row_number, prepared_job, (), identity, namespace)


def _parse_csv(
    content: bytes,
    *,
    max_file_size_bytes: int,
    max_rows: int,
    column_mapping: Mapping[str, str] | None,
) -> tuple[list[_ParsedRow], list[str]]:
    if not isinstance(content, bytes):
        raise CsvVacancyImportError("CSV content must be provided as bytes")
    if len(content) > max_file_size_bytes:
        raise CsvVacancyImportError("CSV exceeds the configured file-size limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CsvVacancyImportError("CSV must be valid UTF-8 text") from error
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers:
            raise CsvVacancyImportError("CSV must include a header row")
        mapping, unmapped = _column_mapping(headers, column_mapping)
        parsed_rows: list[_ParsedRow] = []
        for row_number, row in enumerate(reader, start=2):
            if row is None:
                continue
            if None in row:
                raise CsvVacancyImportError("CSV row has more values than its header")
            if all(not (value or "").strip() for value in row.values()):
                continue
            values = {
                target: (row.get(header) or "")
                for header, target in mapping.items()
            }
            parsed_rows.append(_row_job(values, row_number))
            if len(parsed_rows) > max_rows:
                raise CsvVacancyImportError("CSV exceeds the configured row limit")
    except csv.Error as error:
        raise CsvVacancyImportError("CSV quoting or row structure is malformed") from error
    return parsed_rows, unmapped


def _limits(
    max_file_size_bytes: int | None, max_rows: int | None
) -> tuple[int, int]:
    settings = get_settings()
    size_limit = (
        settings.CSV_IMPORT_MAX_FILE_SIZE_BYTES
        if max_file_size_bytes is None
        else max_file_size_bytes
    )
    row_limit = settings.CSV_IMPORT_MAX_ROWS if max_rows is None else max_rows
    if size_limit < 1 or row_limit < 1:
        raise CsvVacancyImportError("CSV limits must be positive")
    return size_limit, row_limit


def _validate_csv_database_isolation(
    config: C5ExperimentConfig, store: C5ExperimentStore
) -> None:
    settings = get_settings()
    production_url = settings.DATABASE_URL
    production_path: str | None = None
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if production_url.startswith(prefix):
            production_path = production_url[len(prefix) :]
            break
    if production_path is not None:
        isolated_path = config.sqlite_path
        if isolated_path is None or isolated_path == Path(production_path).resolve():
            raise CsvVacancyImportError(
                "CSV import database must be isolated from the application database"
            )
    database_row = store.connection.execute("PRAGMA database_list").fetchone()
    actual_path = (
        Path(database_row["file"]).resolve()
        if database_row and database_row["file"]
        else None
    )
    if actual_path is None and store.read_only and not config.sqlite_path.is_file():
        return
    if actual_path is None or actual_path != config.sqlite_path:
        raise CsvVacancyImportError(
            "CSV store must use the configured isolated SQLite database"
        )


def _existing_job(
    store: C5ExperimentStore, row: _ParsedRow
) -> sqlite3.Row | None:
    assert row.job is not None and row.canonical_identity is not None
    by_identity = store.connection.execute(
        "SELECT id,canonical_key,payload FROM jobs WHERE canonical_key = ?",
        (row.canonical_identity,),
    ).fetchone()
    if by_identity is not None:
        return by_identity
    if row.job.external_id:
        return store.connection.execute(
            "SELECT jobs.id,jobs.canonical_key,jobs.payload "
            "FROM jobs JOIN source_links ON source_links.job_id=jobs.id "
            "WHERE source_links.source_name=? AND source_links.external_id=?",
            (row.job.source_name, row.job.external_id),
        ).fetchone()
    return store.connection.execute(
        "SELECT jobs.id,jobs.canonical_key,jobs.payload "
        "FROM jobs JOIN source_links ON source_links.job_id=jobs.id "
        "WHERE source_links.source_name=? AND source_links.source_url=?",
        (row.job.source_name, row.job.source_url),
    ).fetchone()


def _source_link(
    connection: sqlite3.Connection, job: NormalizedJob, job_id: str
) -> sqlite3.Row | None:
    if job.external_id:
        link = connection.execute(
            "SELECT id,external_id,source_url,application_url,attribution "
            "FROM source_links WHERE source_name=? AND external_id=?",
            (job.source_name, job.external_id),
        ).fetchone()
        if link is not None:
            return link
    link = connection.execute(
        "SELECT id,external_id,source_url,application_url,attribution "
        "FROM source_links WHERE source_name=? AND job_id=?",
        (job.source_name, job_id),
    ).fetchone()
    if link is not None:
        return link
    if not job.external_id:
        return connection.execute(
            "SELECT id,external_id,source_url,application_url,attribution "
            "FROM source_links WHERE source_name=? AND source_url=?",
            (job.source_name, job.source_url),
        ).fetchone()
    return None


def _source_link_changed(
    connection: sqlite3.Connection, job: NormalizedJob, job_id: str
) -> bool:
    link = _source_link(connection, job, job_id)
    if link is None:
        return False
    return any(
        (
            link["external_id"] != (job.external_id or None),
            link["source_url"] != job.source_url,
            link["application_url"] != job.application_url,
            link["attribution"] != job.attribution,
        )
    )


def _normalized_payload(job: NormalizedJob) -> dict[str, Any]:
    raw = dict(job.raw)
    raw.update(
        {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "country": job.country,
            "province": job.province,
            "city": job.city,
            "remote_eligibility": job.remote_eligibility,
            "description": job.description,
            "requirements": job.requirements,
            "category": job.category,
            "source_name": job.source_name,
            "source_url": job.source_url,
            "application_url": job.application_url,
            "external_id": job.external_id,
            "attribution": job.attribution,
            "posted_at": job.posted_at.isoformat() if job.posted_at else None,
            "deadline": job.deadline.isoformat() if job.deadline else None,
        }
    )
    return raw


def _same_job(existing: sqlite3.Row | None, job: NormalizedJob) -> bool:
    if existing is None:
        return False
    try:
        old = json.loads(existing["payload"])
    except (TypeError, json.JSONDecodeError):
        return False
    new = _normalized_payload(job)
    compared_fields = (
        "title",
        "company",
        "location",
        "country",
        "province",
        "city",
        "remote_eligibility",
        "description",
        "requirements",
        "category",
        "application_url",
        "posted_at",
        "deadline",
    )
    return all(old.get(field) == new.get(field) for field in compared_fields)


def preview_csv_vacancies(
    content: bytes,
    config: C5ExperimentConfig,
    store: C5ExperimentStore,
    *,
    column_mapping: Mapping[str, str] | None = None,
    max_file_size_bytes: int | None = None,
    max_rows: int | None = None,
) -> CsvVacancyPreviewResult:
    """Validate a CSV without writing to the supplied isolated SQLite store."""

    config.validate()
    _validate_csv_database_isolation(config, store)
    size_limit, row_limit = _limits(max_file_size_bytes, max_rows)
    rows, unmapped = _parse_csv(
        content,
        max_file_size_bytes=size_limit,
        max_rows=row_limit,
        column_mapping=column_mapping,
    )
    result = CsvVacancyPreviewResult(total_rows=len(rows), parsed_rows=len(rows))
    result.unmapped_columns = unmapped
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row.reasons:
            if "missing_required_fields" in row.reasons:
                result.missing_required_fields += 1
            if any(
                reason.startswith("invalid_") and reason.endswith("_url")
                for reason in row.reasons
            ):
                result.invalid_urls += 1
            if "location_not_verified_as_zambian" in row.reasons:
                result.unverified_locations += 1
            if "invalid_date" in row.reasons:
                result.invalid_dates += 1
            result.rejected_rows.append(CsvRowIssue(row.row_number, row.reasons))
            continue
        assert row.job is not None and row.canonical_identity is not None
        dedupe_key = (
            row.job.source_name,
            row.job.external_id or row.canonical_identity,
        )
        if dedupe_key in seen:
            result.duplicates_within_file += 1
            result.rejected_rows.append(
                CsvRowIssue(row.row_number, ("duplicate_within_file",))
            )
            continue
        seen.add(dedupe_key)
        existing = _existing_job(store, row)
        action = "new"
        if existing is not None:
            result.duplicates_against_store += 1
            changed = not _same_job(existing, row.job) or _source_link_changed(
                store.connection, row.job, existing["id"]
            )
            action = "update" if changed else "duplicate"
        result.eligible += 1
        result.records.append(
            CsvVacancyPreview(
                row_number=row.row_number,
                title=row.job.title,
                company=row.job.company,
                location=row.job.location,
                source_name=row.job.source_name,
                source_url_domain=urlsplit(row.job.source_url).hostname or "",
                application_url_present=bool(row.job.application_url),
                existing_action=action,
            )
        )
    return result


def import_csv_vacancies(
    content: bytes,
    config: C5ExperimentConfig,
    store: C5ExperimentStore,
    *,
    column_mapping: Mapping[str, str] | None = None,
    max_file_size_bytes: int | None = None,
    max_rows: int | None = None,
    filename: str = "",
    confirmation_id: str | None = None,
) -> CsvVacancyImportResult:
    """Import one CSV batch atomically into the isolated C5 SQLite store."""

    config.validate()
    _validate_csv_database_isolation(config, store)
    size_limit, row_limit = _limits(max_file_size_bytes, max_rows)
    rows, _ = _parse_csv(
        content,
        max_file_size_bytes=size_limit,
        max_rows=row_limit,
        column_mapping=column_mapping,
    )
    batch_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    accepted = updated = duplicates = skipped = rejected = 0
    seen: set[tuple[str, str]] = set()
    connection = store.connection
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS csv_import_batches ("
            "batch_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, created_at TEXT NOT NULL, "
            "content_sha256 TEXT NOT NULL, total_rows INTEGER NOT NULL, accepted INTEGER NOT NULL, "
            "updated INTEGER NOT NULL, duplicates INTEGER NOT NULL, skipped INTEGER NOT NULL, "
            "filename TEXT NOT NULL DEFAULT '', valid_count INTEGER NOT NULL DEFAULT 0, "
            "rejected INTEGER NOT NULL, confirmation_id TEXT)"
        )
        batch_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(csv_import_batches)")
        }
        if "filename" not in batch_columns:
            connection.execute(
                "ALTER TABLE csv_import_batches ADD COLUMN filename TEXT NOT NULL DEFAULT ''"
            )
        if "valid_count" not in batch_columns:
            connection.execute(
                "ALTER TABLE csv_import_batches ADD COLUMN valid_count INTEGER NOT NULL DEFAULT 0"
            )
        if "confirmation_id" not in batch_columns:
            connection.execute(
                "ALTER TABLE csv_import_batches ADD COLUMN confirmation_id TEXT"
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ix_csv_import_batches_confirmation_id "
            "ON csv_import_batches(confirmation_id) WHERE confirmation_id IS NOT NULL"
        )
        if confirmation_id is not None:
            previous = connection.execute(
                "SELECT batch_id,created_at,total_rows,accepted,updated,duplicates,"
                "skipped,rejected FROM csv_import_batches WHERE confirmation_id=?",
                (confirmation_id,),
            ).fetchone()
            if previous is not None:
                connection.rollback()
                return CsvVacancyImportResult(
                    batch_id=previous["batch_id"],
                    accepted=previous["accepted"],
                    updated=previous["updated"],
                    duplicates=previous["duplicates"],
                    skipped=previous["skipped"],
                    rejected=previous["rejected"],
                    total_rows=previous["total_rows"],
                    created_at=previous["created_at"],
                )
        for row in rows:
            if row.reasons:
                rejected += 1
                continue
            assert row.job is not None and row.canonical_identity is not None
            dedupe_key = (
                row.job.source_name,
                row.job.external_id or row.canonical_identity,
            )
            if dedupe_key in seen:
                duplicates += 1
                continue
            seen.add(dedupe_key)

            existing = _existing_job(store, row)
            is_changed = existing is not None and (
                not _same_job(existing, row.job)
                or _source_link_changed(connection, row.job, existing["id"])
            )
            payload = _normalized_payload(row.job)
            payload.update(
                {
                    "csv_import_batch_id": batch_id,
                    "experiment_id": config.experiment_id,
                }
            )
            payload_json = json.dumps(payload, sort_keys=True, default=str)
            now = created_at
            if existing is None:
                job_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO jobs(id,canonical_key,payload,created_at) VALUES (?,?,?,?)",
                    (job_id, row.canonical_identity, payload_json, now),
                )
                accepted += 1
            else:
                job_id = existing["id"]
                connection.execute(
                    "UPDATE jobs SET canonical_key=?,payload=? WHERE id=?",
                    (row.canonical_identity, payload_json, job_id),
                )
                if is_changed:
                    updated += 1
                else:
                    duplicates += 1

            source_link = _source_link(connection, row.job, job_id)
            if source_link is None:
                connection.execute(
                    "INSERT INTO source_links"
                    "(id,job_id,source_name,source_namespace,external_id,source_url,"
                    "application_url,attribution) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        job_id,
                        row.job.source_name,
                        row.source_namespace,
                        row.job.external_id or None,
                        row.job.source_url,
                        row.job.application_url,
                        row.job.attribution,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE source_links SET job_id=?,source_namespace=?,external_id=?,"
                    "source_url=?,application_url=?,attribution=? WHERE id=?",
                    (
                        job_id,
                        row.source_namespace,
                        row.job.external_id or None,
                        row.job.source_url,
                        row.job.application_url,
                        row.job.attribution,
                        source_link["id"],
                    ),
                )
            connection.execute(
                "INSERT INTO observations"
                "(id,job_id,source_name,source_namespace,source_url,external_id,payload_hash,"
                "fetched_at,created_at,retrieval_method,experiment_id,retrieval_run_id,"
                "retrieval_query,provider,http_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    job_id,
                    row.job.source_name,
                    row.source_namespace,
                    row.job.source_url,
                    row.job.external_id or None,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                    now,
                    now,
                    "csv_import",
                    config.experiment_id,
                    batch_id,
                    None,
                    "csv_import",
                    None,
                ),
            )
        connection.execute(
            "INSERT INTO csv_import_batches"
            "(batch_id,experiment_id,created_at,content_sha256,total_rows,accepted,updated,"
            "duplicates,skipped,filename,valid_count,rejected,confirmation_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id,
                config.experiment_id,
                created_at,
                hashlib.sha256(content).hexdigest(),
                len(rows),
                accepted,
                updated,
                duplicates,
                skipped,
                filename,
                len(rows) - rejected,
                rejected,
                confirmation_id,
            ),
        )
        connection.commit()
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise CsvVacancyPersistenceError(
            "CSV import transaction failed; no batch records were committed"
        ) from error
    except Exception:
        connection.rollback()
        raise
    return CsvVacancyImportResult(
        batch_id=batch_id,
        accepted=accepted,
        updated=updated,
        duplicates=duplicates,
        skipped=skipped,
        rejected=rejected,
        total_rows=len(rows),
        created_at=created_at,
    )


def csv_import_history(
    store: C5ExperimentStore,
    experiment_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return import summaries without exposing CSV contents."""

    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='csv_import_batches'"
    ).fetchone()
    if exists is None:
        return []
    columns = {
        row["name"]
        for row in store.connection.execute("PRAGMA table_info(csv_import_batches)")
    }
    filename_expr = "filename" if "filename" in columns else "'' AS filename"
    valid_expr = "valid_count" if "valid_count" in columns else "total_rows-rejected AS valid_count"
    rows = store.connection.execute(
        "SELECT batch_id,experiment_id,created_at,total_rows,accepted,updated,duplicates,"
        f"skipped,rejected,{filename_expr},{valid_expr} FROM csv_import_batches "
        "WHERE experiment_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (experiment_id, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def csv_import_detail(
    store: C5ExperimentStore, experiment_id: str, batch_id: str
) -> dict[str, Any] | None:
    """Return one import summary and source-level counts, without raw job data."""
    for batch in csv_import_history(store, experiment_id, limit=100_000):
        if batch["batch_id"] == batch_id:
            source_rows = store.connection.execute(
                "SELECT source_name,COUNT(*) AS observations FROM observations "
                "WHERE experiment_id=? AND retrieval_run_id=? GROUP BY source_name",
                (experiment_id, batch_id),
            ).fetchall()
            return {
                **batch,
                "sources": [dict(row) for row in source_rows],
                "errors_retained": False,
            }
    return None
