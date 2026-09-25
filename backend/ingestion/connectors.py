"""Connector boundary for the canonical ingestion pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.ingestion.schema import NormalizedJob


class JobSourceConnector(ABC):
    """Pure source adapter; connectors return jobs and never persist them."""

    source_name: str

    @abstractmethod
    async def fetch_jobs(self) -> list[NormalizedJob]:
        """Return normalized jobs without opening a database session."""
