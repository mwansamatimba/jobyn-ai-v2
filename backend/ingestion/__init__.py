"""Controlled, non-network ingestion primitives."""

from backend.ingestion.connectors import JobSourceConnector
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
from backend.ingestion.orchestrator import (
    IngestionAuthorizationError,
    IngestionRunResult,
    run_ingestion,
)
from backend.ingestion.schema import NormalizedJob
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
    "IngestionAuthorizationError",
    "IngestionResult",
    "IngestionRunResult",
    "IngestionSource",
    "JobSourceConnector",
    "NormalizedJob",
    "PermissionStatus",
    "SyntheticJob",
    "SyntheticJobStore",
    "get_source",
    "ingest_synthetic_jobs",
    "run_experiment",
    "run_ingestion",
]
from backend.ingestion.c5 import C5ImportResult, run_c5_experiment_import
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.retrieval import (
    C5HttpClient,
    C5HttpResponse,
    C5RequestGovernor,
    C5RetrievalError,
    C5RetrievalTarget,
    C5ScrapingdogError,
    C5ScrapingdogGoogleJobsProvider,
    GoZambiaJobsParser,
    JobZambiaParser,
    PublicInstitutionParser,
    ZambiaJobParser,
    run_c5_retrieval,
    run_scrapingdog_google_jobs,
)
from backend.ingestion.techmap import (
    TechmapConfig,
    TechmapError,
    TechmapProvider,
    TechmapRunResult,
    run_techmap_once,
)

__all__ = [
    "C5ExperimentConfig",
    "C5HttpClient",
    "C5HttpResponse",
    "C5ImportResult",
    "C5RequestGovernor",
    "C5RetrievalError",
    "C5RetrievalTarget",
    "C5ScrapingdogError",
    "C5ScrapingdogGoogleJobsProvider",
    "GoZambiaJobsParser",
    "JobZambiaParser",
    "PublicInstitutionParser",
    "ZambiaJobParser",
    "run_c5_experiment_import",
    "run_c5_retrieval",
    "run_scrapingdog_google_jobs",
]

__all__ += [
    "TechmapConfig",
    "TechmapError",
    "TechmapProvider",
    "TechmapRunResult",
    "run_techmap_once",
]
