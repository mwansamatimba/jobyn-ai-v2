"""Isolated C5 import adapter; never calls the production orchestrator."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime

from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.processing import is_zambia_location, sanitize_text, validate_url
from backend.ingestion.schema import NormalizedJob
from backend.ingestion.sources import SOURCE_CAPS, namespace_for
from backend.ingestion.store import C5ExperimentStore

TOTAL_CAP = 50
REQUEST_BUDGET = 40
EXPERIMENT_SOURCES = frozenset(SOURCE_CAPS)
PROHIBITED_KEYS = {
    "candidate_name", "candidate_email", "candidate_phone", "candidate_cv",
    "candidate_profile", "applicant", "applicant_record", "cookies",
    "authentication_token", "auth_token", "credentials", "private_messages",
    "user_account", "password", "session_cookie",
}


def sanitize_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_payload(item)
            for key, item in value.items()
            if str(key).lower() not in PROHIBITED_KEYS
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def _payload_hash(job: NormalizedJob) -> str:
    return hashlib.sha256(
        json.dumps(job.raw, sort_keys=True, default=str).encode()
    ).hexdigest()


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


class C5ImportResult:
    def __init__(self, accepted: int, rejected: int, store: C5ExperimentStore):
        self.accepted = accepted
        self.rejected = rejected
        self.store = store


def run_c5_experiment_import(
    config: C5ExperimentConfig,
    source_name: str,
    payloads: Iterable[Mapping[str, object]],
    *,
    institution_id: str | None = None,
    retrieval_requests: int = 1,
    store: C5ExperimentStore | None = None,
    retrieval_run_id: str | None = None,
    retrieval_query: str | None = None,
    retrieval_provider: str | None = None,
    http_status: int | None = None,
) -> C5ImportResult:
    config.validate()
    if source_name.startswith("zambian_public_institution:"):
        source_name, institution_id = source_name.split(":", 1)
    if source_name not in EXPERIMENT_SOURCES:
        raise ValueError("unknown or non-experiment source")
    namespace = namespace_for(source_name, institution_id)
    db = store or C5ExperimentStore(config.sqlite_path)
    if (
        retrieval_requests < 0
        or db.request_count(config.experiment_id) + retrieval_requests > REQUEST_BUDGET
    ):
        raise ValueError("C5 retrieval request budget exceeded")
    if source_name == "zambiajob" and db.count_primary_jobs() >= 25:
        raise ValueError("zambiajob is fallback-only after primary sources reach 25 records")
    for _ in range(retrieval_requests):
        if not db.reserve_request(
            config.experiment_id, source_name, config.retrieval_method, REQUEST_BUDGET
        ):
            raise ValueError("C5 retrieval request budget exceeded")
    db.record_audit(
        config,
        source_name,
        namespace,
        retrieval_run_id or str(uuid.uuid4()),
        SOURCE_CAPS[source_name],
    )
    accepted = rejected = 0
    for raw in payloads:
        if db.count_jobs() >= TOTAL_CAP or db.count_source(source_name) >= SOURCE_CAPS[source_name]:
            rejected += 1
            continue
        try:
            payload = dict(sanitize_payload(dict(raw)))
            payload["source_name"] = source_name
            application_url = validate_url(str(payload.get("application_url") or "")) or ""
            source_url = validate_url(str(payload.get("source_url") or ""))
            if not source_url:
                raise ValueError("a source URL is required")
            job = NormalizedJob(
                title=str(payload.get("title") or "").strip(),
                company=str(payload.get("company") or payload.get("company_name") or "").strip(),
                application_url=application_url,
                source_name=source_name,
                external_id=str(payload.get("external_id") or payload.get("id") or "").strip(),
                source_url=source_url,
                attribution=str(payload.get("attribution") or f"Source: {source_name}").strip(),
                location=str(payload.get("location") or "").strip(),
                country=str(payload.get("country") or "").strip(),
                province=str(payload.get("province") or "").strip(),
                city=str(payload.get("city") or "").strip(),
                remote_eligibility=str(payload.get("remote_eligibility") or "").strip(),
                description=sanitize_text(str(payload.get("description") or "")),
                requirements=sanitize_text(str(payload.get("requirements") or "")),
                category=str(payload.get("category") or ""),
                posted_at=_optional_datetime(payload.get("posted_at")),
                deadline=_optional_datetime(payload.get("deadline")),
                raw=payload,
            )
            if (
                not job.title
                or not job.company
                or not is_zambia_location(job.location, job.country)
            ):
                raise ValueError("invalid vacancy")
            job.category = job.category or "other"
            job.raw = {**payload, "payload_hash": _payload_hash(job)}
            db.persist(
                job,
                namespace,
                config.experiment_id,
                config.retrieval_method,
                retrieval_run_id or "",
                retrieval_query=retrieval_query,
                provider=retrieval_provider,
                http_status=http_status,
            )
            accepted += 1
        except (TypeError, ValueError):
            rejected += 1
    return C5ImportResult(accepted, rejected, db)
