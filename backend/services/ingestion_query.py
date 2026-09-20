"""PostgreSQL query helpers for the ingestion-engine Jobs API.

Provides search_jobs_query() (full-text + filters) and get_filter_options().
Uses only the existing async SQLAlchemy session — no new dependencies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import Job

logger = logging.getLogger(__name__)


async def search_jobs_query(
    session: AsyncSession,
    *,
    q: str | None,
    location: str | None,
    province: str | None,
    country: str | None,
    remote: bool | None,
    employment_type: str | None,
    seniority: str | None,
    category: str | None,
    company: str | None,
    source: str | None,
    date_posted: str | None,
    deadline: str | None,
    sort: str,
    offset: int,
    limit: int,
) -> tuple[list[Job], int]:
    """Search active jobs with optional filters.

    Returns (jobs, total_count).
    """
    stmt = select(Job).where(
        Job.is_active.is_(True),
        Job.deleted_at.is_(None),
    )

    # ------------------------------------------------------------------
    # Text search — title, company_name, description (case-insensitive)
    # ------------------------------------------------------------------
    if q and q.strip():
        q_clean = q.strip().lower()
        # Use ilike for portability (works on both SQLite and PostgreSQL)
        pattern = f"%{q_clean}%"
        stmt = stmt.where(
            or_(
                func.lower(Job.title).like(pattern),
                func.lower(Job.company_name).like(pattern),
                func.lower(Job.description).like(pattern),
            )
        )

    # ------------------------------------------------------------------
    # Location filters
    # ------------------------------------------------------------------
    if location and location.strip():
        pat = f"%{location.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Job.location).like(pat),
                func.lower(Job.city).like(pat),
                func.lower(Job.province).like(pat),
                func.lower(Job.country).like(pat),
            )
        )

    if province and province.strip():
        stmt = stmt.where(
            func.lower(Job.province).like(f"%{province.strip().lower()}%")
        )

    if country and country.strip():
        stmt = stmt.where(
            func.lower(Job.country) == country.strip().lower()
        )

    # ------------------------------------------------------------------
    # Remote filter
    # ------------------------------------------------------------------
    if remote is True:
        stmt = stmt.where(
            Job.remote_eligibility.in_([
                "zambia_eligible", "africa_eligible", "global", "restrictions_unclear"
            ])
        )
    elif remote is False:
        stmt = stmt.where(
            or_(
                Job.remote_eligibility == "not_remote",
                Job.remote_eligibility.is_(None),
            )
        )

    # ------------------------------------------------------------------
    # Employment type / seniority / category / company / source
    # ------------------------------------------------------------------
    if employment_type and employment_type.strip():
        stmt = stmt.where(Job.employment_type == employment_type.strip())

    if seniority and seniority.strip():
        stmt = stmt.where(Job.experience_level == seniority.strip())

    if category and category.strip():
        stmt = stmt.where(Job.category == category.strip())

    if company and company.strip():
        stmt = stmt.where(
            func.lower(Job.company_name).like(f"%{company.strip().lower()}%")
        )

    if source and source.strip():
        stmt = stmt.where(Job.source_name == source.strip())

    # ------------------------------------------------------------------
    # Date filters
    # ------------------------------------------------------------------
    if date_posted and date_posted.strip():
        try:
            dp = datetime.fromisoformat(date_posted.strip()).replace(tzinfo=timezone.utc)
            stmt = stmt.where(Job.posted_at >= dp)
        except ValueError:
            logger.debug("Ignoring invalid date_posted: %s", date_posted)

    if deadline and deadline.strip():
        try:
            dl = datetime.fromisoformat(deadline.strip()).replace(tzinfo=timezone.utc)
            stmt = stmt.where(Job.deadline >= dl)
        except ValueError:
            logger.debug("Ignoring invalid deadline: %s", deadline)

    # ------------------------------------------------------------------
    # Total count (same filters, no pagination)
    # ------------------------------------------------------------------
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one() or 0

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------
    if sort == "deadline":
        stmt = stmt.order_by(Job.deadline.asc().nulls_last())
    elif sort == "company":
        stmt = stmt.order_by(Job.company_name.asc())
    else:  # "recent" (default)
        stmt = stmt.order_by(
            Job.posted_at.desc().nulls_last(),
            Job.created_at.desc(),
        )

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    jobs = list(result.scalars().all())

    return jobs, int(total)


async def get_filter_options(session: AsyncSession) -> dict[str, Any]:
    """Return distinct filter values for frontend dropdowns."""
    base = select(Job).where(Job.is_active.is_(True), Job.deleted_at.is_(None))

    async def _distinct(col: Any) -> list[str]:
        stmt = select(func.distinct(col)).select_from(base.subquery()).where(col.isnot(None))
        result = await session.execute(stmt)
        return sorted(str(v) for v in result.scalars().all() if v)

    return {
        "provinces": await _distinct(Job.province),
        "countries": await _distinct(Job.country),
        "employment_types": await _distinct(Job.employment_type),
        "experience_levels": await _distinct(Job.experience_level),
        "categories": await _distinct(Job.category),
        "sources": await _distinct(Job.source_name),
    }
