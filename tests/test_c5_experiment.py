from __future__ import annotations

import pytest
from backend.ingestion.c5 import run_c5_experiment_import
from backend.ingestion.config import C5ConfigurationError, C5ExperimentConfig
from backend.ingestion.sources import SOURCE_REGISTRY
from backend.ingestion.store import C5ExperimentStore


def vacancy(number: int = 1, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "title": f"Engineer {number}",
        "company": "Fixture Company",
        "location": "Lusaka, Zambia",
        "country": "Zambia",
        "source_url": f"https://gozambiajobs.com/jobs/{number}",
        "application_url": f"https://gozambiajobs.com/apply/{number}",
        "external_id": str(number),
        "attribution": "Fixture source",
    }
    value.update(overrides)
    return value


def config(tmp_path) -> C5ExperimentConfig:
    return C5ExperimentConfig(
        experiment_id="c5-test",
        environment="non_production",
        experiment_mode=True,
        database_url=f"sqlite:///{tmp_path / 'c5.db'}",
    )


def test_configuration_fails_closed(tmp_path) -> None:
    with pytest.raises(C5ConfigurationError):
        C5ExperimentConfig("x", "production", True, f"sqlite:///{tmp_path / 'x.db'}").validate()
    with pytest.raises(C5ConfigurationError):
        C5ExperimentConfig("x", "non_production", True, "postgresql://prod/db").validate()


def test_import_isolated_and_minimized(tmp_path) -> None:
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    result = run_c5_experiment_import(
        cfg,
        "go_zambia_jobs",
        [vacancy(candidate_email="private@example.com", credentials="secret")],
        store=store,
    )
    assert result.accepted == 1
    assert store.count_jobs() == 1
    payload = store.connection.execute("SELECT payload FROM jobs").fetchone()[0]
    assert "private@example.com" not in payload
    assert "secret" not in payload


def test_location_cap_dedupe_and_attribution(tmp_path) -> None:
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    rows = [vacancy(), vacancy(), vacancy(2, location="Remote - Africa", country="")]
    result = run_c5_experiment_import(cfg, "go_zambia_jobs", rows, store=store)
    assert result.accepted == 2
    assert result.rejected == 1
    link = store.connection.execute("SELECT attribution FROM source_links").fetchone()
    assert link["attribution"] == "Fixture source"


def test_total_cap_and_source_state(tmp_path) -> None:
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    first = run_c5_experiment_import(
        cfg, "go_zambia_jobs", [vacancy(i) for i in range(50)], store=store
    )
    assert first.accepted == 15
    for source in (
        "go_zambia_jobs",
        "jobzambia",
        "zambian_public_institution",
        "zambiajob",
        "linkedin",
        "indeed",
    ):
        assert SOURCE_REGISTRY[source].permission_status.value == "permission_required"
        assert SOURCE_REGISTRY[source].active is False


def test_total_cap_is_cumulative_across_allowlisted_sources(tmp_path) -> None:
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    assert run_c5_experiment_import(
        cfg, "go_zambia_jobs", [vacancy(i) for i in range(15)], store=store
    ).accepted == 15
    assert run_c5_experiment_import(
        cfg, "jobzambia", [vacancy(i + 100) for i in range(15)], store=store
    ).accepted == 15
    assert run_c5_experiment_import(
        cfg,
        "zambian_public_institution",
        [vacancy(i + 200) for i in range(10)],
        institution_id="zamstats",
        store=store,
    ).accepted == 10
    assert store.count_jobs() == 40


def test_fallback_allows_below_25_and_blocks_at_25(tmp_path) -> None:
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    assert run_c5_experiment_import(
        cfg, "go_zambia_jobs", [vacancy(i) for i in range(15)], store=store
    ).accepted == 15
    assert run_c5_experiment_import(
        cfg, "jobzambia", [vacancy(i + 100) for i in range(9)], store=store
    ).accepted == 9
    assert run_c5_experiment_import(
        cfg, "zambiajob", [vacancy(i + 200) for i in range(10)], store=store
    ).accepted == 10

    store = C5ExperimentStore(tmp_path / "blocked.db")
    run_c5_experiment_import(
        cfg, "go_zambia_jobs", [vacancy(i) for i in range(15)], store=store
    )
    run_c5_experiment_import(
        cfg, "jobzambia", [vacancy(i + 100) for i in range(10)], store=store
    )
    with pytest.raises(ValueError, match="fallback-only"):
        run_c5_experiment_import(cfg, "zambiajob", [vacancy()], store=store)


def test_observation_provenance_and_cross_source_linking(tmp_path) -> None:
    cfg = config(tmp_path)
    store = C5ExperimentStore(cfg.sqlite_path)
    first = vacancy()
    second = vacancy(
        source_url="https://jobzambia.com/jobs/1",
        application_url=first["application_url"],
        external_id="jobzambia-1",
    )
    run_c5_experiment_import(
        cfg, "go_zambia_jobs", [first, first], retrieval_run_id="run-go", store=store
    )
    run_c5_experiment_import(
        cfg, "jobzambia", [second], retrieval_run_id="run-job", store=store
    )
    assert store.count_jobs() == 1
    assert store.connection.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 2
    assert store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3
    rows = store.connection.execute(
        "SELECT source_name, source_namespace, experiment_id, retrieval_run_id "
        "FROM observations ORDER BY retrieval_run_id"
    ).fetchall()
    assert rows[0]["retrieval_run_id"] == "run-go"
    assert rows[-1]["retrieval_run_id"] == "run-job"


@pytest.mark.parametrize(
    "location",
    (
        "Lusaka, Zambia",
        "Ndola, Zambia",
        "Zambia",
        "Remote - Zambia",
    ),
)
def test_zambia_locations_are_retained(tmp_path, location: str) -> None:
    cfg = config(tmp_path)
    result = run_c5_experiment_import(
        cfg,
        "go_zambia_jobs",
        [vacancy(location=location)],
        store=C5ExperimentStore(cfg.sqlite_path),
    )
    assert result.accepted == 1


@pytest.mark.parametrize(
    "location",
    (
        "Kenya",
        "South Africa",
        "Nigeria",
        "United Kingdom",
        "United States",
        "Remote - Kenya",
        "Remote - Africa",
        "Remote - Global",
        "Worldwide",
    ),
)
def test_non_zambia_locations_are_rejected(tmp_path, location: str) -> None:
    cfg = config(tmp_path)
    result = run_c5_experiment_import(
        cfg,
        "go_zambia_jobs",
        [vacancy(location=location, country="")],
        store=C5ExperimentStore(cfg.sqlite_path),
    )
    assert result.accepted == 0
