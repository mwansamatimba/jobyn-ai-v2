"""Compliance registry used by production and controlled experiments.

This registry is deliberately data-only. It does not grant permission to
retrieve anything; experiment sources are inactive and may only consume local
synthetic fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PermissionStatus(StrEnum):
    """Permission state recorded for an ingestion source."""

    OFFICIAL_API = "official_api"
    OFFICIAL_FEED = "official_feed"
    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REQUIRED = "permission_required"


@dataclass(frozen=True, slots=True)
class IngestionSource:
    """Immutable source policy snapshot."""

    name: str
    permission_status: PermissionStatus
    active: bool
    experiment_allowed: bool = False
    fallback_only: bool = False


def _source(
    name: str,
    permission_status: PermissionStatus,
    *,
    active: bool,
    experiment_allowed: bool = False,
    fallback_only: bool = False,
) -> IngestionSource:
    return IngestionSource(
        name=name,
        permission_status=permission_status,
        active=active,
        experiment_allowed=experiment_allowed,
        fallback_only=fallback_only,
    )


SOURCE_REGISTRY: dict[str, IngestionSource] = {
    "official_api": _source("official_api", PermissionStatus.OFFICIAL_API, active=True),
    "official_feed": _source("official_feed", PermissionStatus.OFFICIAL_FEED, active=True),
    "permission_granted": _source(
        "permission_granted", PermissionStatus.PERMISSION_GRANTED, active=True
    ),
    "linkedin": _source("linkedin", PermissionStatus.PERMISSION_REQUIRED, active=False),
    "indeed": _source("indeed", PermissionStatus.PERMISSION_REQUIRED, active=False),
    "go_zambia_jobs": _source(
        "go_zambia_jobs",
        PermissionStatus.PERMISSION_REQUIRED,
        active=False,
        experiment_allowed=True,
    ),
    "jobzambia": _source(
        "jobzambia",
        PermissionStatus.PERMISSION_REQUIRED,
        active=False,
        experiment_allowed=True,
    ),
    "zambian_public_institution": _source(
        "zambian_public_institution",
        PermissionStatus.PERMISSION_REQUIRED,
        active=False,
        experiment_allowed=True,
    ),
    "zambiajob": _source(
        "zambiajob",
        PermissionStatus.PERMISSION_REQUIRED,
        active=False,
        experiment_allowed=True,
        fallback_only=True,
    ),
}

ALLOWLISTED_EXPERIMENT_SOURCES = frozenset(
    name for name, source in SOURCE_REGISTRY.items() if source.experiment_allowed
)


def get_source(name: str) -> IngestionSource | None:
    """Return a policy snapshot, or ``None`` for an unknown source."""

    return SOURCE_REGISTRY.get(name.strip().lower())


def classify_source(name: str) -> IngestionSource:
    """Resolve a source without treating unknown names as safe defaults."""

    source = get_source(name)
    if source is None:
        raise ValueError(f"unknown ingestion source: {name}")
    return source
