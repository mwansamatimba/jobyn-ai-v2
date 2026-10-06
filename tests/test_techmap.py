from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from backend.core.config import Settings
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.retrieval import C5HttpResponse
from backend.ingestion.sources import SOURCE_REGISTRY
from backend.ingestion.store import C5ExperimentStore
from backend.ingestion.techmap import (
    TECHMAP_DOMAIN,
    TECHMAP_ENDPOINT,
    TECHMAP_PROVIDER,
    TECHMAP_RETRIEVAL_METHOD,
    TECHMAP_SOURCE_NAME,
    TechmapConfig,
    TechmapError,
    TechmapProvider,
    run_techmap_once,
)

# Fake transport mirrors the bounded C5 HTTP interface.
# ruff: noqa: ASYNC109

FIXTURE = Path(__file__).parent / "fixtures" / "techmap_zambia_jobs.json"
API_SECRET = "fixture-techmap-secret"


class FakeTransport:
    def __init__(self, response: C5HttpResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, float, int]] = []

    async def request(self, url: str, *, timeout: float, max_bytes: int) -> C5HttpResponse:
        self.calls.append((url, timeout, max_bytes))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        if self.response.url == TECHMAP_ENDPOINT:
            return replace(self.response, url=url)
        return self.response


def c5_config(tmp_path, experiment_id: str = "techmap-test") -> C5ExperimentConfig:
    return C5ExperimentConfig(
        experiment_id=experiment_id,
        environment="non_production",
        experiment_mode=True,
        database_url=f"sqlite:///{tmp_path / 'techmap.db'}",
        retrieval_method=TECHMAP_RETRIEVAL_METHOD,
    )


def response(body: bytes | None = None, status: int = 200, *, url: str = TECHMAP_ENDPOINT):
    return C5HttpResponse(
        url=url,
        status_code=status,
        headers={"content-type": "application/json"},
        body=body if body is not None else FIXTURE.read_bytes(),
    )


def provider_config(**overrides) -> TechmapConfig:
    values = {
        "api_key": API_SECRET,
        "country_code": "zm",
        "page_size": 10,
        "max_pages": 1,
        "is_duplicate": False,
    }
    values.update(overrides)
    return TechmapConfig(**values)


@pytest.mark.asyncio
async def test_request_shape_auth_header_one_request_and_normalized_fields(tmp_path):
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(response())
    result = await run_techmap_once(
        cfg,
        provider_config(),
        retrieval_run_id="run-1",
        transport=transport,
        store=db,
    )

    assert len(transport.calls) == 1
    requested_url = transport.calls[0][0]
    parsed = urlparse(requested_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == TECHMAP_ENDPOINT
    assert parse_qs(parsed.query) == {
        "countryCode": ["zm"],
        "isDuplicate": ["false"],
        "page": ["1"],
        "pageSize": ["10"],
    }
    assert result.http_status == 200
    assert result.total_count == 2
    assert result.returned_count == 2
    assert result.processed_count == 2
    assert result.accepted == 1
    assert result.rejected == 1
    assert db.count_jobs() == 1
    request = db.connection.execute(
        "SELECT source_name,retrieval_method,retrieval_query,provider "
        "FROM retrieval_requests"
    ).fetchone()
    assert tuple(request) == (
        TECHMAP_SOURCE_NAME,
        TECHMAP_RETRIEVAL_METHOD,
        "countryCode=zm",
        TECHMAP_PROVIDER,
    )

    job = json.loads(db.connection.execute("SELECT payload FROM jobs").fetchone()[0])
    assert job["title"] == "Software Engineer"
    assert job["company"] == "Lusaka Technology Ltd"
    assert job["location"] == "Lusaka"
    assert job["country"] == "Zambia"
    assert job["city"] == "Lusaka"
    assert job["description"] == "Build and maintain services."
    assert job["requirements"] == "Python; FastAPI\nDegree in Computer Science"
    assert job["source_name"] == TECHMAP_SOURCE_NAME
    assert job["source_url"] == "https://example.zm/jobs/techmap-zm-001"
    assert job["application_url"] == "https://example.zm/jobs/techmap-zm-001/apply"
    assert job["posted_at"] == "2026-09-30T08:00:00Z"
    assert job["deadline"] == ""
    assert job["category"] == "technology"
    assert "via Example Careers" in job["attribution"]
    assert job["techmap_provenance"]["techmap_date_active"] == "2026-09-30T08:00:00Z"
    assert job["techmap_provenance"]["techmap_date_expired"] == "2026-10-30T23:59:59Z"
    assert db.connection.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 1
    assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert db.connection.execute("SELECT COUNT(*) FROM experiment_metadata").fetchone()[0] == 1
    assert db.connection.execute("SELECT COUNT(*) FROM compliance_events").fetchone()[0] == 1


def test_api_key_required_and_does_not_appear_in_repr_or_error(tmp_path):
    with pytest.raises(TechmapError) as exc:
        TechmapConfig(api_key=None).validate()
    assert exc.value.code == "missing_api_key"
    assert API_SECRET not in str(exc.value)
    cfg = provider_config()
    assert API_SECRET not in repr(cfg)
    assert TECHMAP_DOMAIN == ("daily-international-job-postings.p.rapidapi.com",)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"country_code": "ke"}, "invalid_country"),
        ({"page_size": 11}, "invalid_page_size"),
        ({"max_pages": 2}, "pagination_disabled"),
        ({"is_duplicate": True}, "invalid_duplicate_mode"),
    ],
)
def test_first_run_configuration_is_bounded(overrides, code):
    with pytest.raises(TechmapError) as exc:
        provider_config(**overrides).validate()
    assert exc.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "bad_request"),
        (401, "authentication_error"),
        (403, "authentication_error"),
        (429, "rate_limited"),
        (500, "http_error"),
        (503, "http_error"),
    ],
)
async def test_http_errors_are_classified_without_credentials_or_body(
    tmp_path, status: int, code: str
):
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    body_marker = b"sensitive-response-body-marker"
    transport = FakeTransport(response(body_marker, status=status))
    with pytest.raises(TechmapError) as exc:
        await run_techmap_once(
            cfg,
            provider_config(),
            retrieval_run_id="run-error",
            transport=transport,
            store=db,
        )
    assert exc.value.code == code
    assert API_SECRET not in str(exc.value)
    assert body_marker.decode() not in str(exc.value)
    assert len(transport.calls) == 1
    assert db.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"malformed-body-marker", "malformed_json"),
        (b"[]", "unexpected_response"),
        (b'{"totalCount":0}', "missing_result"),
        (b'{"result":{}}', "unexpected_response"),
    ],
)
async def test_malformed_response_classification_is_sanitized(tmp_path, body: bytes, code: str):
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    with pytest.raises(TechmapError) as exc:
        await run_techmap_once(
            cfg,
            provider_config(),
            retrieval_run_id="run-malformed",
            transport=FakeTransport(response(body)),
            store=db,
        )
    assert exc.value.code == code
    assert "malformed-body-marker" not in str(exc.value)
    assert API_SECRET not in str(exc.value)


@pytest.mark.asyncio
async def test_timeout_is_one_governed_attempt_and_never_retried(tmp_path):
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(error=TimeoutError(API_SECRET))
    with pytest.raises(TechmapError) as exc:
        await run_techmap_once(
            cfg,
            provider_config(),
            retrieval_run_id="run-timeout",
            transport=transport,
            store=db,
        )
    assert exc.value.code == "timeout"
    assert API_SECRET not in str(exc.value)
    assert len(transport.calls) == 1
    assert db.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
async def test_one_request_budget_blocks_second_provider_invocation(tmp_path):
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(response())
    await run_techmap_once(
        cfg, provider_config(), retrieval_run_id="run-once", transport=transport, store=db
    )
    with pytest.raises(TechmapError) as exc:
        await run_techmap_once(
            cfg,
            provider_config(),
            retrieval_run_id="run-twice",
            transport=transport,
            store=db,
        )
    assert exc.value.code == "request_budget_exhausted"
    assert len(transport.calls) == 1
    assert db.request_count(cfg.experiment_id) == 1


@pytest.mark.asyncio
async def test_repeated_record_deduplicates_via_c5_canonical_identity(tmp_path):
    fixture = json.loads(FIXTURE.read_bytes())
    fixture["totalCount"] = 2
    fixture["result"] = [fixture["result"][0], fixture["result"][0]]
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    result = await run_techmap_once(
        cfg,
        provider_config(),
        retrieval_run_id="run-duplicates",
        transport=FakeTransport(response(json.dumps(fixture).encode())),
        store=db,
    )
    assert result.accepted == 2
    assert db.count_jobs() == 1
    assert db.count_source(TECHMAP_SOURCE_NAME) == 1
    assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_repeated_runs_are_idempotent_and_keep_observations(tmp_path):
    cfg1 = c5_config(tmp_path, "techmap-run-1")
    db = C5ExperimentStore(cfg1.sqlite_path)
    await run_techmap_once(
        cfg1,
        provider_config(),
        retrieval_run_id="run-1",
        transport=FakeTransport(response()),
        store=db,
    )
    cfg2 = c5_config(tmp_path, "techmap-run-2")
    second = await run_techmap_once(
        cfg2,
        provider_config(),
        retrieval_run_id="run-2",
        transport=FakeTransport(response()),
        store=db,
    )
    assert second.accepted == 1
    assert db.count_jobs() == 1
    assert db.count_source(TECHMAP_SOURCE_NAME) == 1
    assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_maximum_ten_records_and_no_second_page(tmp_path):
    fixture = json.loads(FIXTURE.read_bytes())
    record = fixture["result"][0]
    fixture["result"] = [
        {**record, "id": f"job-{index}", "applicationUrl": f"https://example.zm/apply/{index}"}
        for index in range(12)
    ]
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    transport = FakeTransport(response(json.dumps(fixture).encode()))
    result = await run_techmap_once(
        cfg,
        provider_config(),
        retrieval_run_id="run-cap",
        transport=transport,
        store=db,
    )
    assert result.returned_count == 12
    assert result.processed_count == 10
    assert result.rejected == 2
    assert len(transport.calls) == 1
    assert parse_qs(urlparse(transport.calls[0][0]).query)["page"] == ["1"]
    assert "nextPage" not in parse_qs(urlparse(transport.calls[0][0]).query)
    assert db.count_jobs() == 10


@pytest.mark.asyncio
async def test_redirect_is_rejected_and_application_url_is_never_requested(
    tmp_path, monkeypatch
):
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    redirect = response(
        b'{"result":[]}',
        status=302,
        url="https://elsewhere.invalid/redirect",
    )
    transport = FakeTransport(redirect)
    with pytest.raises(TechmapError) as exc:
        await run_techmap_once(
            cfg, provider_config(), retrieval_run_id="run-redirect", transport=transport, store=db
        )
    assert exc.value.code == "domain_guard"
    assert len(transport.calls) == 1
    assert all("example.zm" not in call[0] for call in transport.calls)

    def forbidden_network(*args, **kwargs):
        pytest.fail("provider attempted an application URL request")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_network)


def test_transport_adds_rapidapi_headers_without_exposing_key(monkeypatch):
    from backend.ingestion.retrieval import UrllibC5Transport

    transport = TechmapProvider(config=provider_config()).transport
    assert isinstance(transport, UrllibC5Transport)
    assert transport.headers["X-RapidAPI-Key"] == API_SECRET
    assert transport.headers["X-RapidAPI-Host"] == TECHMAP_DOMAIN[0]
    assert API_SECRET not in repr(provider_config())


def test_urllib_request_uses_get_and_rapidapi_headers_without_url_key(monkeypatch):
    import io
    import urllib.request

    from backend.ingestion.retrieval import UrllibC5Transport

    class FakeResponse:
        status = 200

        def __init__(self):
            self.headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size: int) -> bytes:
            return io.BytesIO(b'{"result":[]}').read(size)

        def geturl(self) -> str:
            return TECHMAP_ENDPOINT

    class FakeOpener:
        def open(self, request, timeout):
            assert request.get_method() == "GET"
            assert request.get_header("X-rapidapi-key") == API_SECRET
            assert request.get_header("X-rapidapi-host") == TECHMAP_DOMAIN[0]
            assert API_SECRET not in request.full_url
            return FakeResponse()

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: FakeOpener(),
    )
    result = UrllibC5Transport(
        {
            "X-RapidAPI-Key": API_SECRET,
            "X-RapidAPI-Host": TECHMAP_DOMAIN[0],
        }
    )._request(
        TECHMAP_ENDPOINT + "?countryCode=zm&page=1&pageSize=10",
        timeout=1,
        max_bytes=100,
        headers={
            "X-RapidAPI-Key": API_SECRET,
            "X-RapidAPI-Host": TECHMAP_DOMAIN[0],
        },
    )
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_production_database_and_wrong_store_are_rejected(tmp_path, monkeypatch):
    cfg = c5_config(tmp_path)
    production_db = tmp_path / "production.sqlite"
    monkeypatch.setattr(
        "backend.ingestion.techmap.get_settings",
        lambda: Settings(DATABASE_URL=f"sqlite+aiosqlite:///{production_db}"),
    )
    same_path_config = replace(cfg, database_url=f"sqlite:///{production_db}")
    same_store = C5ExperimentStore(production_db)
    with pytest.raises(TechmapError) as prod_error:
        await run_techmap_once(
            same_path_config,
            provider_config(),
            retrieval_run_id="run-prod",
            transport=FakeTransport(response()),
            store=same_store,
        )
    assert prod_error.value.code == "database_isolation"

    wrong_store = C5ExperimentStore(tmp_path / "other.sqlite")
    with pytest.raises(TechmapError) as mismatch:
        await run_techmap_once(
            cfg,
            provider_config(),
            retrieval_run_id="run-wrong-db",
            transport=FakeTransport(response()),
            store=wrong_store,
        )
    assert mismatch.value.code == "database_isolation"


@pytest.mark.asyncio
async def test_provider_does_not_call_production_ingestion_or_csv_importer(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Techmap path called an unrelated ingestion path")

    monkeypatch.setattr("backend.ingestion.orchestrator.run_ingestion", forbidden)
    monkeypatch.setattr("backend.ingestion.csv_import.import_csv_vacancies", forbidden)
    cfg = c5_config(tmp_path)
    db = C5ExperimentStore(cfg.sqlite_path)
    result = await run_techmap_once(
        cfg,
        provider_config(),
        retrieval_run_id="run-independent",
        transport=FakeTransport(response()),
        store=db,
    )
    assert result.accepted == 1


def test_techmap_source_remains_inactive_and_permission_required():
    policy = SOURCE_REGISTRY[TECHMAP_SOURCE_NAME]
    assert policy.permission_status.value == "permission_required"
    assert policy.active is False
