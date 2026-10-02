"""SQLite-only persistence for C5 experiment records and audit metadata."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_links (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_name TEXT NOT NULL,
 source_namespace TEXT NOT NULL, external_id TEXT, source_url TEXT NOT NULL,
 application_url TEXT, attribution TEXT,
 UNIQUE(source_name, external_id), FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_name TEXT NOT NULL,
 source_namespace TEXT NOT NULL, source_url TEXT NOT NULL, external_id TEXT,
 payload_hash TEXT NOT NULL, fetched_at TEXT NOT NULL, created_at TEXT NOT NULL,
 retrieval_method TEXT NOT NULL, experiment_id TEXT NOT NULL, retrieval_run_id TEXT NOT NULL,
 retrieval_query TEXT, provider TEXT, http_status INTEGER,
 FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS experiment_metadata (
 experiment_id TEXT PRIMARY KEY, retrieval_run_id TEXT NOT NULL, source_name TEXT NOT NULL,
 source_namespace TEXT NOT NULL, requested_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
 environment TEXT NOT NULL, authorization_status TEXT NOT NULL, production_use INTEGER NOT NULL,
 record_cap INTEGER NOT NULL, source_cap INTEGER NOT NULL,
 retrieval_request_budget INTEGER NOT NULL,
 retrieval_method TEXT NOT NULL, operator TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_requests (
 id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL,
 requested_at TEXT NOT NULL, source_name TEXT NOT NULL, retrieval_method TEXT NOT NULL,
 retrieval_query TEXT, provider TEXT
);
CREATE TABLE IF NOT EXISTS compliance_events (
 id TEXT PRIMARY KEY, event_type TEXT NOT NULL, experiment_id TEXT NOT NULL,
 source_name TEXT NOT NULL, source_namespace TEXT NOT NULL, environment TEXT NOT NULL,
 authorization_status TEXT NOT NULL, production_use INTEGER NOT NULL, record_cap INTEGER NOT NULL,
 retrieval_request_budget INTEGER NOT NULL, retrieval_method TEXT NOT NULL,
 requested_at TEXT NOT NULL, operator TEXT NOT NULL
);
"""


class C5ExperimentStore:
    def __init__(self, database_path: Path | None):
        self.connection = sqlite3.connect(
            ":memory:" if database_path is None else database_path,
            check_same_thread=False,
        )
        self.database_path = (
            None if database_path is None else Path(database_path).resolve()
        )
        self.read_only = False
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._ensure_observation_provenance_columns()
        self._ensure_request_provenance_columns()

    @classmethod
    def open_readonly(cls, database_path: Path) -> C5ExperimentStore:
        """Open an existing experiment database without schema or data writes."""
        path = Path(database_path).resolve()
        if not path.is_file():
            store = cls(None)
            store.read_only = True
            return store
        uri = f"file:{quote(path.as_posix(), safe='/')}?mode=ro"
        store = cls.__new__(cls)
        store.connection = sqlite3.connect(
            uri,
            uri=True,
            check_same_thread=False,
        )
        store.connection.row_factory = sqlite3.Row
        store.database_path = path
        store.read_only = True
        return store

    def _ensure_observation_provenance_columns(self) -> None:
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(observations)")
        }
        for name, definition in (
            ("retrieval_query", "TEXT"),
            ("provider", "TEXT"),
            ("http_status", "INTEGER"),
        ):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE observations ADD COLUMN {name} {definition}"
                )
        self.connection.commit()

    def _ensure_request_provenance_columns(self) -> None:
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(retrieval_requests)")
        }
        for name in ("retrieval_query", "provider"):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE retrieval_requests ADD COLUMN {name} TEXT"
                )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def count_jobs(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def count_source(self, source_name: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM source_links WHERE source_name = ?", (source_name,)
            ).fetchone()[0]
        )

    def count_primary_jobs(self) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM source_links WHERE source_name IN (?,?,?)",
                ("go_zambia_jobs", "jobzambia", "zambian_public_institution"),
            ).fetchone()[0]
        )

    def request_count(self, experiment_id: str) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM retrieval_requests WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()[0]
        )

    def reserve_request(
        self,
        experiment_id: str,
        source_name: str,
        method: str,
        maximum: int,
        *,
        retrieval_query: str | None = None,
        provider: str | None = None,
    ) -> bool:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.request_count(experiment_id) >= maximum:
                self.connection.rollback()
                return False
            self.connection.execute(
                "INSERT INTO retrieval_requests"
                "(experiment_id, requested_at, source_name, retrieval_method,"
                "retrieval_query,provider) VALUES (?,?,?,?,?,?)",
                (
                    experiment_id,
                    datetime.now(UTC).isoformat(),
                    source_name,
                    method,
                    retrieval_query,
                    provider,
                ),
            )
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def persist(
        self,
        job,
        namespace: str,
        experiment_id: str,
        retrieval_method: str,
        retrieval_run_id: str,
        *,
        retrieval_query: str | None = None,
        provider: str | None = None,
        http_status: int | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        canonical_key = sha256(
            "|".join(
                part.strip().lower()
                for part in (
                    job.application_url,
                    job.company,
                    job.title,
                    job.location,
                )
            ).encode()
        ).hexdigest()
        payload_hash = sha256(
            json.dumps(job.raw, sort_keys=True, default=str).encode()
        ).hexdigest()
        row = self.connection.execute(
            "SELECT id FROM jobs WHERE canonical_key = ?", (canonical_key,)
        ).fetchone()
        job_id = row["id"] if row else str(uuid.uuid4())
        if row is None:
            self.connection.execute(
                "INSERT INTO jobs(id, canonical_key, payload, created_at) VALUES (?,?,?,?)",
                (job_id, canonical_key, json.dumps(job.raw, default=str), now),
            )
        link = self.connection.execute(
            "SELECT id FROM source_links WHERE source_name = ? AND "
            "((external_id = ?) OR (external_id IS NULL AND source_url = ?))",
            (job.source_name, job.external_id or None, job.source_url),
        ).fetchone()
        if link is None:
            self.connection.execute(
                "INSERT INTO source_links"
                "(id,job_id,source_name,source_namespace,external_id,source_url,"
                "application_url,attribution) VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()), job_id, job.source_name, namespace,
                    job.external_id or None, job.source_url, job.application_url,
                    job.attribution,
                ),
            )
        self.connection.execute(
            "INSERT INTO observations"
            "(id,job_id,source_name,source_namespace,source_url,external_id,payload_hash,"
            "fetched_at,created_at,retrieval_method,experiment_id,retrieval_run_id,"
            "retrieval_query,provider,http_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), job_id, job.source_name, namespace, job.source_url,
                job.external_id or None, payload_hash, now, now, retrieval_method,
                experiment_id,
                retrieval_run_id,
                retrieval_query,
                provider,
                http_status,
            ),
        )
        self.connection.commit()

    def record_audit(
        self, config, source_name: str, namespace: str, run_id: str, source_cap: int
    ) -> None:
        now = datetime.now(UTC).isoformat()
        values = (
            config.experiment_id, run_id, source_name, namespace, now, now,
            "non_production", "unresolved", 0, 50, source_cap, 40,
            config.retrieval_method, config.operator,
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO experiment_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        self.connection.execute(
            "INSERT INTO compliance_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), "controlled_ingestion_experiment", config.experiment_id,
                source_name, namespace, "non_production", "unresolved", 0, 50, 40,
                config.retrieval_method, now, config.operator,
            ),
        )
        self.connection.commit()
