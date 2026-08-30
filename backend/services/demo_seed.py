"""Idempotent seeding of curated Jobyn demo jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.data.demo_jobs import DEMO_JOBS
from backend.models.enums import EmploymentType, ExperienceLevel, JobSource, LocationType
from backend.models.job import Job

logger = logging.getLogger(__name__)


async def seed_demo_jobs(session: AsyncSession) -> int:
    """Insert missing curated demo jobs; safe to run repeatedly."""
    result = await session.execute(select(Job.title, Job.company_name))
    existing_keys = {(row[0], row[1]) for row in result.all()}
    now = datetime.now(timezone.utc)
    pending: list[Job] = []

    for title, company, location, location_type, employment_type, experience_level, currency, salary_min, salary_max, description in DEMO_JOBS:
        key = (title, company)
        if key in existing_keys:
            continue
        pending.append(Job(
            title=title,
            company_name=company,
            location=location,
            location_type=LocationType(location_type),
            employment_type=EmploymentType(employment_type),
            experience_level=ExperienceLevel(experience_level),
            salary_currency=currency,
            salary_min=salary_min,
            salary_max=salary_max,
            description=description,
            posted_at=now,
            is_active=True,
            source=JobSource.INTERNAL,
            external_url=None,
        ))
        existing_keys.add(key)

    if not pending:
        return 0
    session.add_all(pending)
    await session.commit()
    logger.info("Seeded %d curated Jobyn demo jobs.", len(pending))
    return len(pending)


__all__ = ["seed_demo_jobs"]
