"""Guarded Stage C4 runner.

The runner accepts only caller-supplied fixture records. There is intentionally
no HTTP client, production session, or environment bypass in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.ingestion.engine import IngestionResult, SyntheticJobStore, ingest_synthetic_jobs
from backend.ingestion.sources import ALLOWLISTED_EXPERIMENT_SOURCES, PermissionStatus, get_source

EXPERIMENT_CAP = 50


class ExperimentConfigurationError(ValueError):
    """Raised when a C4 safety invariant is not satisfied."""


@dataclass(slots=True)
class ExperimentState:
    """Cumulative state keyed by a stable experiment identifier."""

    consumed_by_id: dict[str, int] = field(default_factory=dict)

    def remaining(self, experiment_id: str) -> int:
        return max(0, EXPERIMENT_CAP - self.consumed_by_id.get(experiment_id, 0))

    def consume(self, experiment_id: str, count: int) -> None:
        self.consumed_by_id[experiment_id] = (
            self.consumed_by_id.get(experiment_id, 0) + count
        )


def run_experiment(
    records: list[dict[str, Any]],
    *,
    source_name: str,
    experiment_id: str,
    store: SyntheticJobStore | None = None,
    environment: str = "test",
    experiment_mode: bool = False,
    state: ExperimentState | None = None,
) -> IngestionResult:
    """Run one bounded fixture batch under the C4 policy."""

    if environment != "test":
        raise ExperimentConfigurationError("C4 experiments require ENVIRONMENT=test")
    if not experiment_mode:
        raise ExperimentConfigurationError("C4 experiments require experiment_mode=true")
    if not experiment_id.strip():
        raise ExperimentConfigurationError("experiment_id is required")

    source = get_source(source_name)
    if source is None or source_name not in ALLOWLISTED_EXPERIMENT_SOURCES:
        raise ExperimentConfigurationError(f"source is not allowlisted for C4: {source_name}")
    if source.permission_status is not PermissionStatus.PERMISSION_REQUIRED or source.active:
        raise ExperimentConfigurationError(
            "experiment source must remain permission_required and inactive"
        )

    state = state or ExperimentState()
    remaining = state.remaining(experiment_id)
    if len(records) > remaining:
        raise ExperimentConfigurationError(
            f"experiment cap exceeded: {len(records)} requested, {remaining} remaining"
        )
    state.consume(experiment_id, len(records))
    return ingest_synthetic_jobs(
        records,
        source_name=source.name,
        store=store or SyntheticJobStore(),
    )
