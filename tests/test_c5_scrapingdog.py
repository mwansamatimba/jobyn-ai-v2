from __future__ import annotations

import json
import socket
import urllib.error
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.retrieval import (
    C5HttpResponse,
    C5RequestGovernor,
    C5RetrievalError,
    C5ScrapingdogError,
    run_scrapingdog_google_jobs,
)
from backend.ingestion.sources import SOURCE_REGISTRY
from backend.ingestion.store import C5ExperimentStore

# Fake transport mirrors the bounded C5 HTTP interface.
# ruff: noqa: ASYNC109

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "c5_scrapingdog_google_jobs.json"


class FakeTransport:
    def __init__(self, responses: list[C5HttpResponse] | None = None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[str] = []

    async def request(self, url: str, *, timeout: float, max_bytes: int) -> C5HttpResponse:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        result = self.responses.pop(0)
        if result.url == "https://api.scrapingdog.com/google_jobs":
            return replace(result, url=url)
        return result


def config(tmp_path, *, api_key: str | None = "fixture-secret") -> C5ExperimentConfig:
    return C5ExperimentConfig(
        experiment_id="scrapingdog-test",
        environment="non_production",
        experiment_mode=True,
        database_url=f"sqlite:///{tmp_path / 'scrapingdog.db'}",
        retrieval_method="controlled_http",
        scrapingdog_api_key=api_key,
    )


def response(body: bytes, *, status: int = 200) -> C5HttpResponse:
    return C5HttpResponse(
        "https://api.scrapingdog.com/google_jobs",
        status,
        {"content-type": "application/json"},
        body,
    )


@pytest.mark.asyncio
async def test_success_maps_fields_zambia_filters_and_persists_provenance(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport([response(FIXTURE_PATH.read_bytes())])

    result = await run_scrapingdog_google_jobs(
        cfg,
        "software engineer Zambia",
        retrieval_run_id="run-1",
        transport=transport,
        store=store,
    )

    assert result.accepted == 2
    assert result.rejected == 1
    assert store.count_jobs() == 2
    assert store.count_source("scrapingdog_google_jobs") == 2
    assert len(transport.calls) == 1
    requested = parse_qs(urlparse(transport.calls[0]).query)
    assert requested == {
        "api_key": ["fixture-secret"],
        "query": ["software engineer Zambia"],
        "country": ["zm"],
    }
    assert all("careers.example.zm" not in url for url in transport.calls)

    observation = store.connection.execute(
        "SELECT source_name,source_url,experiment_id,retrieval_run_id,retrieval_query,"
        "provider,http_status,fetched_at FROM observations ORDER BY source_url LIMIT 1"
    ).fetchone()
    assert observation["source_name"] == "scrapingdog_google_jobs"
    assert observation["source_url"] == "https://www.google.com/search?q=example-job-001"
    assert observation["experiment_id"] == "scrapingdog-test"
    assert observation["retrieval_run_id"] == "run-1"
    assert observation["retrieval_query"] == "software engineer Zambia"
    assert observation["provider"] == "scrapingdog_google_jobs"
    assert observation["http_status"] == 200
    assert observation["fetched_at"]
    request = store.connection.execute(
        "SELECT retrieval_query,provider FROM retrieval_requests"
    ).fetchone()
    assert tuple(request) == ("software engineer Zambia", "scrapingdog_google_jobs")

    payload = json.loads(
        store.connection.execute(
            "SELECT payload FROM jobs WHERE payload LIKE '%google-job-001%'"
        ).fetchone()[0]
    )
    assert payload["application_url"] == "https://careers.example.zm/jobs/001"
    assert payload["retrieval_provider"] == "Scrapingdog Google Jobs API"
    assert payload["retrieval_query"] == "software engineer Zambia"
    assert payload["retrieval_run_id"] == "run-1"
    assert payload["posted_at"] == "2026-09-30"
    assert payload["deadline"] == "2026-10-31"
    assert payload["requirements"] == "Qualifications: Python experience Relevant degree"
    assert "Google Jobs via Scrapingdog" in payload["attribution"]
    assert "fixture-secret" not in json.dumps(payload)
    assert "fixture-secret" not in repr(cfg)
    assert (
        SOURCE_REGISTRY["scrapingdog_google_jobs"].permission_status.value
        == "permission_required"
    )
    assert SOURCE_REGISTRY["scrapingdog_google_jobs"].active is False


@pytest.mark.asyncio
async def test_missing_api_key_fails_before_reserving_request(tmp_path):
    cfg = config(tmp_path, api_key=None)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport()

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg, "jobs Zambia", retrieval_run_id="run-1", transport=transport, store=store
        )

    assert exc_info.value.code == "missing_api_key"
    assert transport.calls == []
    assert store.request_count(cfg.experiment_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "expected_code"),
    [
        (b"not-json-marker", 200, "malformed_json"),
        (b'{"error":"provider-body-marker"}', 200, "api_error"),
        (b'{"jobs_results":null}', 200, "malformed_response"),
        (b'{"jobs_results":[]}', 401, "authentication_error"),
        (b'{"jobs_results":[]}', 403, "authentication_error"),
        (b'{"jobs_results":[]}', 429, "rate_limited"),
        (b'{"jobs_results":[]}', 500, "http_error"),
        (b'{"jobs_results":[]}', 503, "http_error"),
    ],
)
async def test_provider_errors_are_classified_without_body(
    tmp_path, body: bytes, status: int, expected_code: str
):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport([response(body, status=status)])

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg, "jobs Zambia", retrieval_run_id="run-1", transport=transport, store=store
        )

    assert exc_info.value.code == expected_code
    assert "fixture-secret" not in str(exc_info.value)
    assert "provider-body-marker" not in str(exc_info.value)
    assert "not-json-marker" not in str(exc_info.value)
    assert len(transport.calls) == 1
    assert transport.calls[0] not in str(exc_info.value)
    assert store.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
async def test_missing_required_fields_are_rejected_and_empty_results_succeed(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(
        [
            response(
                b'{"jobs_results":[{"company_name":"Employer","location":"Lusaka, Zambia",'
                b'"share_link":"https://google.com/search?q=x"}]}'
            ),
            response(b'{"jobs_results":[]}'),
        ]
    )

    missing = await run_scrapingdog_google_jobs(
        cfg, "jobs Zambia", retrieval_run_id="run-1", transport=transport, store=store
    )
    empty = await run_scrapingdog_google_jobs(
        cfg, "empty query", retrieval_run_id="run-2", transport=transport, store=store
    )

    assert (missing.accepted, missing.rejected) == (0, 1)
    assert (empty.accepted, empty.rejected) == (0, 0)
    assert store.count_jobs() == 0
    assert store.request_count(cfg.experiment_id) == 2


@pytest.mark.asyncio
async def test_duplicate_results_across_queries_keep_each_observation(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture["jobs_results"] = fixture["jobs_results"][:1]
    transport = FakeTransport(
        [response(json.dumps(fixture).encode()), response(json.dumps(fixture).encode())]
    )

    first = await run_scrapingdog_google_jobs(
        cfg, "software engineer Zambia", retrieval_run_id="run-1", transport=transport, store=store
    )
    second = await run_scrapingdog_google_jobs(
        cfg, "engineer Lusaka", retrieval_run_id="run-2", transport=transport, store=store
    )

    assert first.accepted == second.accepted == 1
    assert store.count_jobs() == 1
    assert store.count_source("scrapingdog_google_jobs") == 1
    assert store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
    assert {
        tuple(row)
        for row in store.connection.execute(
            "SELECT retrieval_run_id,retrieval_query FROM observations"
        )
    } == {
        ("run-1", "software engineer Zambia"),
        ("run-2", "engineer Lusaka"),
    }


@pytest.mark.asyncio
async def test_governor_blocks_request_41_and_transport_error_is_redacted(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    governor = C5RequestGovernor(store, cfg.experiment_id)
    for _ in range(40):
        await governor.reserve("scrapingdog_google_jobs")
    transport = FakeTransport()

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg, "jobs Zambia", retrieval_run_id="run-1", transport=transport, store=store
        )
    assert exc_info.value.code == "request_budget_exhausted"
    assert "fixture-secret" not in str(exc_info.value)
    assert transport.calls == []
    assert store.request_count(cfg.experiment_id) == 40

    other_store = C5ExperimentStore(tmp_path / "error.db")
    failing = FakeTransport(error=C5RetrievalError("transport failure: fixture-secret"))
    with pytest.raises(C5ScrapingdogError) as transport_error:
        await run_scrapingdog_google_jobs(
            config(tmp_path, api_key="fixture-secret"),
            "jobs Zambia",
            retrieval_run_id="run-2",
            transport=failing,
            store=other_store,
        )
    assert transport_error.value.code == "guard_failure"
    assert "fixture-secret" not in str(transport_error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (urllib.error.URLError(socket.gaierror("fixture-secret")), "dns_connect_failure"),
        (TimeoutError("fixture-secret"), "timeout"),
        (OSError("fixture-secret"), "unexpected_transport"),
        (
            C5RetrievalError("request URL outside approved domain fixture-secret"),
            "guard_failure",
        ),
    ],
)
async def test_transport_and_guard_errors_are_distinguished_and_redacted(
    tmp_path, failure: Exception, expected_code: str
):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(error=failure)

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg,
            "jobs Zambia",
            retrieval_run_id="run-1",
            transport=transport,
            store=store,
        )

    assert exc_info.value.code == expected_code
    assert "fixture-secret" not in str(exc_info.value)
    assert "api_key=" not in str(exc_info.value)
    assert len(transport.calls) == 1
    assert store.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
async def test_response_size_guard_is_classified_without_exposing_body(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport([response(b"body-marker-" + b"x" * 2_000_000)])

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg,
            "jobs Zambia",
            retrieval_run_id="run-1",
            transport=transport,
            store=store,
        )

    assert exc_info.value.code == "response_too_large"
    assert "fixture-secret" not in str(exc_info.value)
    assert "body-marker" not in str(exc_info.value)
    assert len(transport.calls) == 1
    assert store.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
async def test_redirect_response_is_rejected_without_following_it(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(
        [
            C5HttpResponse(
                "https://unapproved.example/redirect?token=fixture-secret",
                302,
                {"location": "https://unapproved.example/redirect"},
                b"redirect-body-marker",
            )
        ]
    )

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg,
            "jobs Zambia",
            retrieval_run_id="run-1",
            transport=transport,
            store=store,
        )

    assert exc_info.value.code == "domain_guard"
    assert "fixture-secret" not in str(exc_info.value)
    assert "api_key=" not in str(exc_info.value)
    assert "redirect-body-marker" not in str(exc_info.value)
    assert len(transport.calls) == 1
    assert store.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
async def test_same_domain_redirect_status_is_not_followed(tmp_path):
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(
        [
            C5HttpResponse(
                "https://api.scrapingdog.com/google_jobs",
                302,
                {"location": "https://api.scrapingdog.com/another-endpoint"},
                b"redirect-body-marker",
            )
        ]
    )

    with pytest.raises(C5ScrapingdogError) as exc_info:
        await run_scrapingdog_google_jobs(
            cfg,
            "jobs Zambia",
            retrieval_run_id="run-1",
            transport=transport,
            store=store,
        )

    assert exc_info.value.code == "domain_guard"
    assert "redirect-body-marker" not in str(exc_info.value)
    assert len(transport.calls) == 1


def test_urllib_transport_classifies_url_errors_without_exposing_reason(monkeypatch):
    from backend.ingestion.retrieval import C5TransportError, UrllibC5Transport

    class FailedOpener:
        def open(self, request, timeout):
            raise urllib.error.URLError(socket.gaierror("fixture-secret"))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: FailedOpener())

    with pytest.raises(C5TransportError) as exc_info:
        UrllibC5Transport._request(
            "https://api.scrapingdog.com/google_jobs?api_key=fixture-secret",
            timeout=1,
            max_bytes=100,
        )

    assert exc_info.value.code == "dns_connect_failure"
    assert "fixture-secret" not in str(exc_info.value)
    assert "api_key=" not in str(exc_info.value)


def test_existing_c5_schema_gets_provenance_columns(tmp_path):
    import sqlite3

    from backend.ingestion.store import SCHEMA

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("ALTER TABLE observations DROP COLUMN retrieval_query")
    connection.execute("ALTER TABLE observations DROP COLUMN provider")
    connection.execute("ALTER TABLE observations DROP COLUMN http_status")
    connection.execute("ALTER TABLE retrieval_requests DROP COLUMN retrieval_query")
    connection.execute("ALTER TABLE retrieval_requests DROP COLUMN provider")
    connection.close()

    migrated = C5ExperimentStore(path)

    observation_columns = {
        row["name"] for row in migrated.connection.execute("PRAGMA table_info(observations)")
    }
    request_columns = {
        row["name"]
        for row in migrated.connection.execute("PRAGMA table_info(retrieval_requests)")
    }
    assert {"retrieval_query", "provider", "http_status"} <= observation_columns
    assert {"retrieval_query", "provider"} <= request_columns
    assert migrated.reserve_request(
        "scrapingdog-test",
        "scrapingdog_google_jobs",
        "controlled_http",
        40,
        retrieval_query="jobs Zambia",
        provider="scrapingdog_google_jobs",
    )
    assert tuple(
        migrated.connection.execute(
            "SELECT retrieval_query,provider FROM retrieval_requests"
        ).fetchone()
    ) == ("jobs Zambia", "scrapingdog_google_jobs")
