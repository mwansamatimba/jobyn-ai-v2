"""Fail-closed configuration for the isolated C5 experiment path."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class C5ConfigurationError(ValueError):
    """Raised when C5 configuration is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class C5ExperimentConfig:
    experiment_id: str | None = None
    environment: str | None = None
    experiment_mode: bool | None = None
    database_url: str | None = None
    operator: str = "offline-fixture"
    retrieval_method: str = "offline_fixture"
    scrapingdog_api_key: str | None = field(default=None, repr=False)

    def validate(self) -> C5ExperimentConfig:
        if not self.experiment_id or not self.experiment_id.strip():
            raise C5ConfigurationError("experiment_id is required")
        if self.environment != "non_production":
            raise C5ConfigurationError("C5 requires environment=non_production")
        if self.experiment_mode is not True:
            raise C5ConfigurationError("C5 requires experiment_mode=true")
        if not self.database_url:
            raise C5ConfigurationError("an isolated C5 database_url is required")
        parsed = urlparse(self.database_url)
        if parsed.scheme not in {"sqlite", "sqlite+aiosqlite"}:
            raise C5ConfigurationError("C5 only permits an isolated SQLite database")
        if parsed.username or parsed.password or parsed.hostname:
            raise C5ConfigurationError("database URL must not contain credentials or a host")
        if self.sqlite_path is None or self.sqlite_path.name in {"", ".", ".."}:
            raise C5ConfigurationError("database URL must identify an isolated SQLite file")
        return self

    @property
    def sqlite_path(self) -> Path | None:
        if not self.database_url:
            return None
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if self.database_url.startswith(prefix):
                return Path(self.database_url[len(prefix) :]).resolve()
        return None

    @classmethod
    def for_temporary_database(
        cls, experiment_id: str, *, operator: str = "offline-fixture"
    ) -> C5ExperimentConfig:
        path = Path(tempfile.mkdtemp(prefix="jobyn_c5_")) / "experiment.db"
        return cls(
            experiment_id=experiment_id,
            environment="non_production",
            experiment_mode=True,
            database_url=f"sqlite:///{path}",
            operator=operator,
        )

    @classmethod
    def from_environment(cls) -> C5ExperimentConfig:
        mode = os.environ.get("C5_EXPERIMENT_MODE")
        return cls(
            experiment_id=os.environ.get("C5_EXPERIMENT_ID"),
            environment=os.environ.get("C5_ENVIRONMENT"),
            experiment_mode=mode.lower() == "true" if mode else None,
            database_url=os.environ.get("C5_DATABASE_URL"),
            operator=os.environ.get("C5_OPERATOR", "offline-fixture"),
            retrieval_method=os.environ.get("C5_RETRIEVAL_METHOD", "offline_fixture"),
            scrapingdog_api_key=os.environ.get("SCRAPINGDOG_API_KEY"),
        ).validate()
