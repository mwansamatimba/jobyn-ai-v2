"""Pure synthetic-fixture ingestion and Zambia location policy."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_ZAMBIA_TERMS = re.compile(r"\b(zambia|zambian|lusaka|kitwe|ndola|livingstone)\b", re.I)
_REMOTE_GLOBAL_TERMS = re.compile(
    r"\b(worldwide|global|anywhere|africa|all africa|sub[- ]?saharan africa)\b", re.I
)


@dataclass(frozen=True, slots=True)
class SyntheticJob:
    """Normalized job record accepted by the experiment boundary."""

    external_id: str
    title: str
    company_name: str
    location: str
    source_name: str
    source_url: str
    description: str = ""
    attribution: str = ""
    country: str | None = None
    remote_eligibility: str | None = None
    canonical_key: str = ""


@dataclass(slots=True)
class IngestionResult:
    source: str
    fetched: int = 0
    created: int = 0
    duplicates: int = 0
    excluded: int = 0
    invalid: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched": self.fetched,
            "created": self.created,
            "duplicates": self.duplicates,
            "excluded": self.excluded,
            "invalid": self.invalid,
        }


@dataclass(slots=True)
class SyntheticJobStore:
    """Explicitly in-memory store; it cannot touch a production database."""

    jobs: list[SyntheticJob] = field(default_factory=list)

    def add(self, job: SyntheticJob) -> bool:
        key = job.canonical_key or job.source_url or job.external_id
        if any((existing.canonical_key or existing.source_url or existing.external_id) == key
               for existing in self.jobs):
            return False
        self.jobs.append(job)
        return True


def is_zambia_location(location: str, country: str | None = None) -> bool:
    """Keep Zambia jobs while excluding broad remote Africa/global listings."""

    text = " ".join(part for part in (country or "", location) if part).strip()
    if _REMOTE_GLOBAL_TERMS.search(text) and not _ZAMBIA_TERMS.search(text):
        return False
    return bool(_ZAMBIA_TERMS.search(text))


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_synthetic_job(raw: dict[str, Any], source_name: str) -> SyntheticJob | None:
    """Normalize a fixture row without fetching or writing externally."""

    external_id = _text(raw.get("external_id") or raw.get("id"))
    title = _text(raw.get("title"))
    company = _text(raw.get("company_name") or raw.get("company"))
    location = _text(raw.get("location"))
    source_url = _text(raw.get("source_url") or raw.get("url"))
    if not all((external_id, title, company, location, source_url)):
        return None
    canonical_key = hashlib.sha256(
        f"{source_name}:{external_id}".encode()
    ).hexdigest()
    return SyntheticJob(
        external_id=external_id,
        title=title,
        company_name=company,
        location=location,
        source_name=source_name,
        source_url=source_url,
        description=_text(raw.get("description")),
        attribution=_text(raw.get("attribution")) or f"Source: {source_name}",
        country=_text(raw.get("country")) or None,
        remote_eligibility=_text(raw.get("remote_eligibility")) or None,
        canonical_key=canonical_key,
    )


def ingest_synthetic_jobs(
    records: list[dict[str, Any]],
    *,
    source_name: str,
    store: SyntheticJobStore,
) -> IngestionResult:
    """Ingest fixture rows into an explicitly supplied in-memory store."""

    result = IngestionResult(source=source_name, fetched=len(records))
    for raw in records:
        job = normalize_synthetic_job(raw, source_name)
        if job is None:
            result.invalid += 1
        elif not is_zambia_location(job.location, job.country):
            result.excluded += 1
        elif store.add(job):
            result.created += 1
        else:
            result.duplicates += 1
    return result
