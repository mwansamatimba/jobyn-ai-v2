"""Explicitly opt-in Bright Data live smoke test.

Usage:
    BRIGHTDATA_LIVE_TEST=true BRIGHTDATA_TEST_MODE=true \
      python -m backend.ingestion.brightdata_smoke

The existing ingestion permission gate is never bypassed by this script.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from backend.core.config import get_settings
from backend.database.session import async_session_factory
from backend.ingestion.sync import run_sync
from backend.models.ingestion import IngestionSource, JobIngestionSource
from backend.models.job import Job


async def main() -> None:
    settings = get_settings()
    if not settings.BRIGHTDATA_LIVE_TEST:
        raise SystemExit(
            "Refusing live Bright Data call: set BRIGHTDATA_LIVE_TEST=true explicitly."
        )
    if not settings.BRIGHTDATA_TEST_MODE:
        raise SystemExit(
            "Refusing non-controlled Bright Data call: set BRIGHTDATA_TEST_MODE=true."
        )
    if not settings.BRIGHTDATA_API_KEY:
        raise SystemExit("BRIGHTDATA_API_KEY is not configured.")

    async with async_session_factory() as session:
        result = await run_sync(
            session,
            source_names=["linkedin", "indeed"],
        )

        source_ids = (
            await session.execute(
                select(IngestionSource.id, IngestionSource.name).where(
                    IngestionSource.name.in_(["linkedin", "indeed"])
                )
            )
        ).all()
        source_map = {name: source_id for source_id, name in source_ids}

        print("Bright Data ingestion test")
        print("---------------------------")

        for source_result in result.sources:
            permission_required = (
                "permission_status=permission_required" in source_result.error_message
            )

            zambia = non_zambia = 0
            source_id = source_map.get(source_result.source_name)
            if source_id is not None:
                rows = (
                    await session.execute(
                        select(Job)
                        .join(JobIngestionSource, JobIngestionSource.job_id == Job.id)
                        .where(JobIngestionSource.source_id == source_id)
                    )
                ).scalars().all()
                for job in rows:
                    if (job.country or "").lower() == "zambia" or (
                        (job.remote_eligibility or "").value
                        if hasattr(job.remote_eligibility, "value")
                        else str(job.remote_eligibility or "")
                    ) == "zambia_eligible":
                        zambia += 1
                    else:
                        non_zambia += 1

            print(f"{source_result.source_name.title()} fetched:        {source_result.fetched}")
            print(f"Valid records:             {source_result.fetched - source_result.rejected}")
            print(f"Zambia records:            {zambia}")
            print(f"Non-Zambia records:        {non_zambia}")
            print(f"Duplicates:                {source_result.duplicates}")
            print(f"Inserted:                  {source_result.created}")
            print(f"Updated:                   {source_result.updated}")
            print(
                "Permission-required:       "
                f"{source_result.fetched if permission_required else 0}"
            )
            print(f"Errors:                    {source_result.errors}")
            if source_result.error_message:
                print(f"Status detail:             {source_result.error_message}")
            print()

        print(f"Total fetched:             {result.total_fetched}")
        print(f"Total inserted:            {result.total_created}")
        print(f"Total updated:             {result.total_updated}")
        print("Credit usage:              Not exposed by the Bright Data trigger/snapshot response.")


if __name__ == "__main__":
    asyncio.run(main())
