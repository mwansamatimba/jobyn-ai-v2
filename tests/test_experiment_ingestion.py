"""Stage C4 controlled ingestion tests; no HTTP or production DB is used."""

from __future__ import annotations

import pytest
from backend.ingestion.engine import SyntheticJobStore
from backend.ingestion.experiment import (
    ExperimentConfigurationError,
    ExperimentState,
    run_experiment,
)
from backend.ingestion.sources import SOURCE_REGISTRY

from tests.fixtures.stage_c4_synthetic_jobs import STAGE_C4_SYNTHETIC_JOBS


def test_production_sources_remain_permitted_and_active() -> None:
    for name in ("official_api", "official_feed", "permission_granted"):
        source = SOURCE_REGISTRY[name]
        assert source.active is True
        assert source.permission_status.value in {
            "official_api",
            "official_feed",
            "permission_granted",
        }


def test_only_explicit_zambia_sources_are_experiment_allowlisted() -> None:
    assert set(SOURCE_REGISTRY) >= {
        "go_zambia_jobs",
        "jobzambia",
        "zambian_public_institution",
        "zambiajob",
    }
    assert all(
        source.experiment_allowed
        for name, source in SOURCE_REGISTRY.items()
        if name in {"go_zambia_jobs", "jobzambia", "zambian_public_institution", "zambiajob"}
    )
    assert not SOURCE_REGISTRY["zambiajob"].active
    assert SOURCE_REGISTRY["zambiajob"].fallback_only


@pytest.mark.parametrize("source_name", ("linkedin", "indeed", "brightdata"))
def test_non_c4_sources_are_rejected(source_name: str) -> None:
    with pytest.raises(ExperimentConfigurationError):
        run_experiment(
            [],
            source_name=source_name,
            experiment_id="reject",
            experiment_mode=True,
        )


def test_valid_experiment_is_fixture_only_and_preserves_source_state() -> None:
    source = SOURCE_REGISTRY["go_zambia_jobs"]
    before = (source.permission_status, source.active, source.experiment_allowed)
    store = SyntheticJobStore()
    result = run_experiment(
        STAGE_C4_SYNTHETIC_JOBS,
        source_name="go_zambia_jobs",
        experiment_id="valid",
        experiment_mode=True,
        store=store,
    )
    assert result.created == 2
    assert result.excluded == 2
    assert result.to_dict()["source"] == "go_zambia_jobs"
    assert len(store.jobs) == 2
    assert all(job.attribution for job in store.jobs)
    assert all(job.source_url.startswith("https://fixture.invalid/") for job in store.jobs)
    assert before == (source.permission_status, source.active, source.experiment_allowed)


@pytest.mark.parametrize(
    ("environment", "experiment_mode"),
    (("production", True), ("test", False)),
)
def test_disabled_modes_are_rejected(environment: str, experiment_mode: bool) -> None:
    with pytest.raises(ExperimentConfigurationError):
        run_experiment(
            [],
            source_name="jobzambia",
            experiment_id="disabled",
            environment=environment,
            experiment_mode=experiment_mode,
        )


def test_cumulative_cap_is_absolute_across_calls() -> None:
    state = ExperimentState()
    rows = [
        {
            "external_id": str(index),
            "title": "Engineer",
            "company_name": "Fixture Co",
            "location": "Lusaka, Zambia",
            "source_url": f"https://fixture.invalid/{index}",
        }
        for index in range(50)
    ]
    run_experiment(
        rows[:30],
        source_name="jobzambia",
        experiment_id="cap",
        experiment_mode=True,
        state=state,
    )
    run_experiment(
        rows[30:],
        source_name="jobzambia",
        experiment_id="cap",
        experiment_mode=True,
        state=state,
    )
    with pytest.raises(ExperimentConfigurationError):
        run_experiment(
            rows[:1],
            source_name="jobzambia",
            experiment_id="cap",
            experiment_mode=True,
            state=state,
        )


def test_dedupe_is_stable_for_repeated_fixture_records() -> None:
    store = SyntheticJobStore()
    row = STAGE_C4_SYNTHETIC_JOBS[:1]
    first = run_experiment(
        row,
        source_name="zambian_public_institution",
        experiment_id="dedupe-1",
        experiment_mode=True,
        store=store,
    )
    second = run_experiment(
        row,
        source_name="zambian_public_institution",
        experiment_id="dedupe-2",
        experiment_mode=True,
        store=store,
    )
    assert first.created == 1
    assert second.duplicates == 1
