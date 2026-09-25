"""Controlled, non-network ingestion primitives."""

from backend.ingestion.engine import (
    IngestionResult,
    SyntheticJob,
    SyntheticJobStore,
    ingest_synthetic_jobs,
)
from backend.ingestion.experiment import (
    ExperimentConfigurationError,
    ExperimentState,
    run_experiment,
)
from backend.ingestion.sources import (
    ALLOWLISTED_EXPERIMENT_SOURCES,
    IngestionSource,
    PermissionStatus,
    get_source,
)

__all__ = [
    "ALLOWLISTED_EXPERIMENT_SOURCES",
    "ExperimentConfigurationError",
    "ExperimentState",
    "IngestionResult",
    "IngestionSource",
    "PermissionStatus",
    "SyntheticJob",
    "SyntheticJobStore",
    "get_source",
    "ingest_synthetic_jobs",
    "run_experiment",
]
