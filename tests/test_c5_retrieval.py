# The fake transport mirrors the bounded production transport contract.
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio

import pytest
from backend.ingestion.config import C5ExperimentConfig
from backend.ingestion.retrieval import (
    C5HttpClient,
    C5HttpResponse,
    C5RequestGovernor,
    C5RetrievalError,
    C5RetrievalTarget,
    run_c5_retrieval,
)
from backend.ingestion.store import C5ExperimentStore


class FakeTransport:
    def __init__(self, responses: dict[str, C5HttpResponse]):
        self.responses = responses
        self.calls: list[str] = []

    async def request(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> C5HttpResponse:
        self.calls.append(url)
        return self.responses[url]


class FailingTransport:
    async def request(self, url: str, *, timeout: float, max_bytes: int) -> C5HttpResponse:
        raise C5RetrievalError("synthetic transport failure")


def test_target_rejects_unsafe_sources_and_domains() -> None:
    with pytest.raises(C5RetrievalError):
        C5RetrievalTarget("linkedin", "x", "r", "https://evil.example", ("evil.example",), 1)
    with pytest.raises(C5RetrievalError):
        C5RetrievalTarget(
            "go_zambia_jobs",
            "x",
            "r",
            "https://evil.example",
            ("gozambiajobs.com",),
            15,
        )


def test_governor_enforces_persistent_budget(tmp_path) -> None:
    store = C5ExperimentStore(tmp_path / "budget.db")
    governor = C5RequestGovernor(store, "budget")

    async def reserve() -> bool:
        try:
            await governor.reserve("go_zambia_jobs")
            return True
        except C5RetrievalError:
            return False

    async def run_all() -> list[bool]:
        return await asyncio.gather(*(reserve() for _ in range(41)))
    results = asyncio.run(run_all())
    assert sum(results) == 40


@pytest.mark.asyncio
async def test_retrieval_uses_listing_only_and_isolated_store(tmp_path) -> None:
    cfg = C5ExperimentConfig(
        "retrieval-test", "non_production", True, f"sqlite:///{tmp_path / 'c5.db'}"
    )
    listing = "https://gozambiajobs.com/jobs"
    body = b"""
    <article data-field="title">Network Engineer</article>
    <div data-field="company">Fixture Employer</div>
    <div data-field="location">Lusaka, Zambia</div>
    <div data-field="application_url">https://apply.example/job/1</div>
    <div data-field="external_id">job-1</div>
    """
    transport = FakeTransport(
        {listing: C5HttpResponse(listing, 200, {"content-type": "text/html"}, body)}
    )
    store = C5ExperimentStore(cfg.sqlite_path)
    result = await run_c5_retrieval(
        cfg,
        C5RetrievalTarget(
            "go_zambia_jobs",
            "retrieval-test",
            "run-1",
            listing,
            ("gozambiajobs.com",),
            15,
        ),
        transport=transport,
        store=store,
    )
    assert result.accepted == 1
    assert transport.calls == [listing]
    assert store.count_jobs() == 1


@pytest.mark.asyncio
async def test_application_url_is_never_requested(tmp_path) -> None:
    store = C5ExperimentStore(tmp_path / "domain.db")
    governor = C5RequestGovernor(store, "domain")
    transport = FakeTransport({})
    client = C5HttpClient(governor, transport=transport, source_name="go_zambia_jobs")
    with pytest.raises(C5RetrievalError):
        await client.get("https://apply.example/job/1", ("gozambiajobs.com",))
    assert transport.calls == []


@pytest.mark.asyncio
async def test_request_budget_allows_40_rejects_41_and_failed_attempts_consume(
    tmp_path,
) -> None:
    store = C5ExperimentStore(tmp_path / "budget.db")
    governor = C5RequestGovernor(store, "budget")
    for _ in range(39):
        await governor.reserve("go_zambia_jobs")
    assert store.request_count("budget") == 39
    await governor.reserve("go_zambia_jobs")
    assert store.request_count("budget") == 40
    with pytest.raises(C5RetrievalError, match="budget"):
        await governor.reserve("go_zambia_jobs")

    failing_store = C5ExperimentStore(tmp_path / "failed.db")
    failing_governor = C5RequestGovernor(failing_store, "failed")
    client = C5HttpClient(
        failing_governor,
        transport=FailingTransport(),
        source_name="go_zambia_jobs",
    )
    with pytest.raises(C5RetrievalError, match="failure"):
        await client.get("https://gozambiajobs.com/jobs", ("gozambiajobs.com",))
    assert failing_store.request_count("failed") == 1


def test_record_cap_accepts_50_and_rejects_51(tmp_path) -> None:
    from backend.ingestion.c5 import run_c5_experiment_import

    cfg = C5ExperimentConfig(
        "cap-test", "non_production", True, f"sqlite:///{tmp_path / 'cap.db'}"
    )
    store = C5ExperimentStore(cfg.sqlite_path)
    for i in range(49):
        store.connection.execute(
            "INSERT INTO jobs(id, canonical_key, payload, created_at) VALUES (?,?,?,?)",
            (f"seed-{i}", f"seed-key-{i}", "{}", "2026-01-01T00:00:00+00:00"),
        )
    store.connection.commit()
    result = run_c5_experiment_import(
        cfg,
        "zambiajob",
        [
            {
                "title": f"Engineer {i}",
                "company": "Fixture",
                "location": "Lusaka, Zambia",
                "country": "Zambia",
                "source_url": f"https://zambiajob.com/jobs/{i}",
                "application_url": f"https://zambiajob.com/apply/{i}",
                "external_id": str(i),
            }
            for i in range(300, 311)
        ],
        store=store,
    )
    assert result.accepted == 1
    assert result.rejected == 10
    assert store.count_jobs() == 50
