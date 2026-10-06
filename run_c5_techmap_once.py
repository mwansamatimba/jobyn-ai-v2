"""Explicit one-request Techmap C5 runner; never invoke from tests."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.store import C5ExperimentStore
from backend.ingestion.techmap import (
    TECHMAP_MAX_RECORDS,
    TECHMAP_PROVIDER,
    TechmapConfig,
    TechmapError,
    run_techmap_once,
)


def main() -> int:
    c5_config = C5ExperimentConfig.from_environment()
    techmap_config = TechmapConfig.from_environment()
    retrieval_run_id = os.environ.get(
        "C5_RETRIEVAL_RUN_ID",
        f"c5-techmap-zambia-{datetime.now(UTC):%Y%m%dT%H%M%SZ}",
    )
    store = C5ExperimentStore(c5_config.sqlite_path)

    print(f"Provider: {TECHMAP_PROVIDER}")
    print(f"Country: {techmap_config.country_code}")
    print("Page: 1")
    print("Max requests: 1")
    print(f"Max records: {TECHMAP_MAX_RECORDS}")
    print(f"Environment: {c5_config.environment}")
    print(f"Experiment mode: {str(c5_config.experiment_mode).lower()}")
    print(f"API key: {'PRESENT' if techmap_config.api_key else 'ABSENT'}")

    try:
        result = asyncio.run(
            run_techmap_once(
                c5_config,
                techmap_config,
                retrieval_run_id=retrieval_run_id,
                store=store,
            )
        )
    except TechmapError as error:
        print(f"Error classification: {error.code}")
        print(f"Request count: {store.request_count(c5_config.experiment_id or '')}")
        store.close()
        return 1

    print(f"HTTP status: {result.http_status}")
    print(f"API totalCount: {result.total_count}")
    print(f"Returned records: {result.returned_count}")
    print(f"Accepted records: {result.accepted}")
    print(f"Rejected records: {result.rejected}")
    print(f"Persisted jobs: {store.count_jobs()}")
    print(f"Persisted source links: {store.count_source('techmap_daily_international')}")
    observation_count = store.connection.execute(
        "SELECT COUNT(*) FROM observations WHERE experiment_id=?",
        (c5_config.experiment_id,),
    ).fetchone()[0]
    print(f"Persisted observations: {observation_count}")
    print(f"Request count: {store.request_count(c5_config.experiment_id or '')}")
    print(f"Experiment ID: {c5_config.experiment_id}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
