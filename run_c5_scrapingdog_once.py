import asyncio

from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.retrieval import UrllibC5Transport, run_scrapingdog_google_jobs

config = C5ExperimentConfig.from_environment()

result = asyncio.run(
    run_scrapingdog_google_jobs(
        config,
        "jobs in Zambia",
        retrieval_run_id="c5-scrapingdog-zambia-20261002-01",
        transport=UrllibC5Transport(),
    )
)

print(f"Accepted: {result.accepted}; rejected: {result.rejected}")
print(f"Canonical jobs: {result.store.count_jobs()}")

result.store.close()
