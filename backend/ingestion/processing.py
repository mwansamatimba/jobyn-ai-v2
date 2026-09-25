"""Pure validation, sanitization, classification, and identity helpers."""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from backend.ingestion.engine import is_zambia_location
from backend.ingestion.schema import NormalizedJob


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def sanitize_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def validate_url(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    return value.strip()


def classify_category(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    if "technician" in text or "technical" in text:
        return "skilled_trades"
    if any(term in text for term in ("engineer", "developer", "software")):
        return "technology"
    return "other"


def canonical_key(job: NormalizedJob) -> str:
    identity = "|".join(
        part.strip().lower()
        for part in (job.application_url, job.company, job.title, job.location)
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def prepare_job(job: NormalizedJob) -> tuple[NormalizedJob, str] | None:
    """Return a sanitized, classified job and canonical identity, or reject it."""

    title = " ".join(job.title.split())
    company = " ".join(job.company.split())
    application_url = validate_url(job.application_url)
    if not title or not company or not application_url:
        return None
    location = " ".join(job.location.split())
    if not is_zambia_location(location, job.country):
        return None
    sanitized = NormalizedJob(
        title=title,
        company=company,
        application_url=application_url,
        source_name=job.source_name,
        external_id=job.external_id.strip(),
        source_url=validate_url(job.source_url) if job.source_url else "",
        attribution=job.attribution.strip() or f"Source: {job.source_name}",
        location=location,
        country=job.country.strip(),
        province=job.province.strip(),
        city=job.city.strip(),
        remote_eligibility=job.remote_eligibility.strip(),
        description=sanitize_text(job.description),
        requirements=sanitize_text(job.requirements),
        category=job.category or classify_category(title, job.description),
        posted_at=job.posted_at,
        deadline=job.deadline,
        raw=job.raw,
    )
    return sanitized, canonical_key(sanitized)
