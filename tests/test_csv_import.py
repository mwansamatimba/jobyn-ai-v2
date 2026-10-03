from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend.core.config import Settings
from backend.ingestion.config import C5ConfigurationError, C5ExperimentConfig
from backend.ingestion.csv_import import (
    CsvVacancyImportError,
    CsvVacancyPersistenceError,
    csv_import_history,
    import_csv_vacancies,
    preview_csv_vacancies,
)
from backend.ingestion.sources import SOURCE_REGISTRY
from backend.ingestion.store import C5ExperimentStore

HEADERS = (
    "title,company,location,description,source_name,source_url,application_url,"
    "external_id,country,province,city,remote_eligibility,requirements,posted_at,"
    "deadline,category,attribution\n"
)


def config(tmp_path) -> C5ExperimentConfig:
    return C5ExperimentConfig(
        experiment_id="csv-import-test",
        environment="non_production",
        experiment_mode=True,
        database_url=f"sqlite:///{tmp_path / 'csv-import.db'}",
        retrieval_method="csv_import",
    )


def store(tmp_path) -> C5ExperimentStore:
    return C5ExperimentStore(tmp_path / "csv-import.db")


def csv_bytes(*rows: str, header: str = HEADERS, bom: bool = False) -> bytes:
    content = header + "".join(f"{row}\n" for row in rows)
    return (("\ufeff" if bom else "") + content).encode("utf-8")


def row(
    *,
    title: str = "Software Engineer",
    company: str = "Zed Tech",
    location: str = "Lusaka, Zambia",
    description: str = "Build software",
    source_name: str = "go_zambia_jobs",
    source_url: str = "https://gozambiajobs.com/jobs/1",
    application_url: str = "https://careers.example.zm/apply/1",
    external_id: str = "job-1",
    country: str = "Zambia",
    province: str = "Lusaka",
    city: str = "Lusaka",
    remote_eligibility: str = "",
    requirements: str = "Python",
    posted_at: str = "2026-09-01",
    deadline: str = "2026-10-01",
    category: str = "",
    attribution: str = "Original publisher",
) -> str:
    values = (
        title,
        company,
        location,
        description,
        source_name,
        source_url,
        application_url,
        external_id,
        country,
        province,
        city,
        remote_eligibility,
        requirements,
        posted_at,
        deadline,
        category,
        attribution,
    )
    return ",".join(f'"{value.replace(chr(34), chr(34) * 2)}"' for value in values)


def table_counts(db: C5ExperimentStore) -> tuple[int, ...]:
    return tuple(
        db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "jobs",
            "source_links",
            "observations",
            "retrieval_requests",
            "experiment_metadata",
            "compliance_events",
        )
    )


def test_valid_csv_import_is_persisted_with_attribution_and_provenance(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    content = csv_bytes(row())

    preview = preview_csv_vacancies(content, cfg, db)
    assert preview.total_rows == 1
    assert preview.parsed_rows == 1
    assert preview.eligible == 1
    assert preview.records[0].source_url_domain == "gozambiajobs.com"
    assert preview.records[0].application_url_present is True
    assert preview.records[0].existing_action == "new"

    result = import_csv_vacancies(content, cfg, db)
    assert (result.accepted, result.updated, result.duplicates, result.rejected) == (1, 0, 0, 0)
    assert db.count_jobs() == 1
    link = db.connection.execute(
        "SELECT source_url,application_url,attribution FROM source_links"
    ).fetchone()
    assert tuple(link) == (
        "https://gozambiajobs.com/jobs/1",
        "https://careers.example.zm/apply/1",
        "Original publisher",
    )
    observation = db.connection.execute(
        "SELECT retrieval_method,experiment_id,retrieval_run_id,provider FROM observations"
    ).fetchone()
    assert tuple(observation) == ("csv_import", cfg.experiment_id, result.batch_id, "csv_import")
    payload = json.loads(db.connection.execute("SELECT payload FROM jobs").fetchone()[0])
    assert payload["csv_import_batch_id"] == result.batch_id
    assert csv_import_history(db, cfg.experiment_id)[0]["batch_id"] == result.batch_id


def test_utf8_bom_aliases_quoted_commas_and_multiline_descriptions(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    alias_header = (
        "job_title,employer,job_location,job_description,source,listing_url,apply_link,"
        "job_id,country\n"
    )
    content = (
        "\ufeff"
        + alias_header
        + '"Developer","Employer, Ltd","Lusaka, Zambia","First line,\nsecond line",'
        '"go_zambia_jobs","https://gozambiajobs.com/jobs/2",'
        '"https://careers.example.zm/apply/2","job-2","Zambia"\n'
    ).encode("utf-8")
    result = preview_csv_vacancies(content, cfg, db)
    assert result.eligible == 1
    assert result.records[0].company == "Employer, Ltd"
    imported = import_csv_vacancies(content, cfg, db)
    payload = json.loads(db.connection.execute("SELECT payload FROM jobs").fetchone()[0])
    assert imported.accepted == 1
    assert payload["description"] == "First line, second line"


def test_unknown_headers_require_explicit_mapping(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    header = (
        "role_name,employer_label,place,details,publisher,listing,apply\n"
    )
    content = (
        header
        + '"Engineer","Example","Kitwe, Zambia","Description","go_zambia_jobs",'
        '"https://gozambiajobs.com/a","https://example.zm/apply"\n'
    ).encode()
    with pytest.raises(CsvVacancyImportError, match="missing required columns"):
        preview_csv_vacancies(content, cfg, db)
    mapping = {
        "role_name": "title",
        "employer_label": "company",
        "place": "location",
        "details": "description",
        "publisher": "source_name",
        "listing": "source_url",
        "apply": "application_url",
    }
    assert preview_csv_vacancies(content, cfg, db, column_mapping=mapping).eligible == 1


def test_missing_values_invalid_urls_dates_and_non_zambia_are_reported(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    content = csv_bytes(
        row(application_url="", posted_at="not-a-date"),
        row(location="Remote - Africa", external_id="job-2", country=""),
        row(title="", external_id="job-3"),
    )
    result = preview_csv_vacancies(content, cfg, db)
    assert result.total_rows == 3
    assert result.invalid_urls == 1
    assert result.invalid_dates == 1
    assert result.unverified_locations == 1
    assert result.missing_required_fields == 2
    assert result.eligible == 0
    assert [issue.row_number for issue in result.rejected_rows] == [2, 3, 4]


def test_duplicate_rows_in_file_and_existing_store_are_detected_and_idempotent(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    content = csv_bytes(row(), row())
    preview = preview_csv_vacancies(content, cfg, db)
    assert preview.eligible == 1
    assert preview.duplicates_within_file == 1
    first = import_csv_vacancies(content, cfg, db)
    assert (first.accepted, first.duplicates, first.rejected) == (1, 1, 0)
    before = db.count_jobs()
    second_preview = preview_csv_vacancies(csv_bytes(row()), cfg, db)
    assert second_preview.duplicates_against_store == 1
    assert second_preview.records[0].existing_action == "duplicate"
    second = import_csv_vacancies(csv_bytes(row()), cfg, db)
    assert (second.accepted, second.updated, second.duplicates) == (0, 0, 1)
    assert db.count_jobs() == before == 1
    assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_concurrent_confirmation_id_is_idempotent(tmp_path) -> None:
    cfg = config(tmp_path)
    content = csv_bytes(row())
    first_store = store(tmp_path)
    second_store = store(tmp_path)

    def import_with_confirmation(database):
        return import_csv_vacancies(
            content,
            cfg,
            database,
            confirmation_id="same-preview-confirmation",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.map(
                import_with_confirmation,
                (first_store, second_store),
            )
        assert first.batch_id == second.batch_id
        assert first.accepted == second.accepted == 1
        assert first_store.count_jobs() == 1
        assert first_store.count_source("go_zambia_jobs") == 1
        assert (
            first_store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            == 1
        )
        assert (
            first_store.connection.execute(
                "SELECT COUNT(*) FROM csv_import_batches"
            ).fetchone()[0]
            == 1
        )
    finally:
        first_store.close()
        second_store.close()


def test_repeat_upload_updates_changed_content_and_deadline(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    assert import_csv_vacancies(csv_bytes(row()), cfg, db).accepted == 1
    changed = row(description="Updated description", deadline="2026-11-01")
    preview = preview_csv_vacancies(csv_bytes(changed), cfg, db)
    assert preview.records[0].existing_action == "update"
    result = import_csv_vacancies(csv_bytes(changed), cfg, db)
    assert (result.accepted, result.updated, result.duplicates) == (0, 1, 0)
    payload = json.loads(db.connection.execute("SELECT payload FROM jobs").fetchone()[0])
    assert payload["description"] == "Updated description"
    assert payload["deadline"].startswith("2026-11-01")
    assert db.count_jobs() == 1


def test_application_url_and_external_id_changes_refresh_existing_source_link(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    import_csv_vacancies(csv_bytes(row()), cfg, db)
    changed_url = row(application_url="https://careers.example.zm/apply/changed")
    refreshed = import_csv_vacancies(csv_bytes(changed_url), cfg, db)
    assert refreshed.updated == 1
    assert db.count_jobs() == 1
    changed_id = row(
        external_id="new-id",
        application_url="https://careers.example.zm/apply/changed",
    )
    refreshed_id = import_csv_vacancies(csv_bytes(changed_id), cfg, db)
    assert refreshed_id.updated == 1
    assert db.count_jobs() == 1
    link = db.connection.execute(
        "SELECT external_id,application_url FROM source_links"
    ).fetchone()
    assert tuple(link) == ("new-id", "https://careers.example.zm/apply/changed")


def test_same_canonical_job_under_two_sources_keeps_both_source_links(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    first = row()
    second = row(
        source_name="jobzambia",
        source_url="https://jobzambia.com/jobs/1",
        external_id="other-source-id",
    )
    result = import_csv_vacancies(csv_bytes(first, second), cfg, db)
    assert (result.accepted, result.updated, result.duplicates) == (1, 0, 1)
    assert db.count_jobs() == 1
    assert db.connection.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 2


def test_removed_rows_are_not_deleted_by_a_refresh(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    import_csv_vacancies(
        csv_bytes(
            row(),
            row(
                title="Data Analyst",
                external_id="job-2",
                application_url="https://careers.example.zm/apply/2",
            ),
        ),
        cfg,
        db,
    )
    import_csv_vacancies(csv_bytes(row()), cfg, db)
    assert db.count_jobs() == 2


@pytest.mark.parametrize(
    "source_name",
    ("linkedin", "indeed", "unknown_source", "brightdata"),
)
def test_disallowed_sources_are_rejected_without_changing_authorization(
    tmp_path, source_name: str
) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    states = {
        name: (source.permission_status, source.active)
        for name, source in SOURCE_REGISTRY.items()
    }
    result = preview_csv_vacancies(csv_bytes(row(source_name=source_name)), cfg, db)
    assert result.eligible == 0
    assert result.rejected_rows[0].reasons == ("source_not_allowed_for_isolated_import",)
    assert states == {
        name: (source.permission_status, source.active)
        for name, source in SOURCE_REGISTRY.items()
    }


def test_personal_data_columns_are_rejected(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    header = HEADERS.rstrip("\n") + ",candidate_email\n"
    with pytest.raises(CsvVacancyImportError, match="prohibited personal-data"):
        preview_csv_vacancies(csv_bytes(row(), header=header), cfg, db)


def test_camel_case_personal_data_columns_are_rejected(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    header = HEADERS.rstrip("\n") + ",candidateEmail\n"
    with pytest.raises(CsvVacancyImportError, match="prohibited personal-data"):
        preview_csv_vacancies(csv_bytes(row(), header=header), cfg, db)


def test_import_row_and_file_size_limits_are_enforced(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    with pytest.raises(CsvVacancyImportError, match="row limit"):
        preview_csv_vacancies(csv_bytes(row(), row(external_id="job-2")), cfg, db, max_rows=1)
    with pytest.raises(CsvVacancyImportError, match="file-size limit"):
        preview_csv_vacancies(csv_bytes(row()), cfg, db, max_file_size_bytes=10)


def test_malformed_csv_and_non_utf8_are_rejected_safely(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    with pytest.raises(CsvVacancyImportError, match="malformed"):
        preview_csv_vacancies((HEADERS + '"unterminated').encode(), cfg, db)
    with pytest.raises(CsvVacancyImportError, match="UTF-8"):
        preview_csv_vacancies(b"\xff\xfe", cfg, db)


def test_preview_performs_no_database_writes(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    before = table_counts(db)
    preview_csv_vacancies(csv_bytes(row()), cfg, db)
    assert table_counts(db) == before
    assert db.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='csv_import_batches'"
    ).fetchone() is None


def test_failed_database_write_rolls_back_entire_batch(tmp_path) -> None:
    cfg = config(tmp_path)
    db = store(tmp_path)
    db.connection.execute(
        "CREATE TRIGGER reject_observation BEFORE INSERT ON observations "
        "BEGIN SELECT RAISE(ABORT, 'synthetic database failure'); END"
    )
    db.connection.commit()
    before = table_counts(db)
    with pytest.raises(CsvVacancyPersistenceError, match="no batch records were committed"):
        import_csv_vacancies(csv_bytes(row()), cfg, db)
    assert table_counts(db) == before
    assert db.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='csv_import_batches'"
    ).fetchone() is None


def test_production_database_url_is_rejected_and_application_links_are_not_fetched(
    tmp_path, monkeypatch
) -> None:
    cfg = config(tmp_path)
    with pytest.raises(C5ConfigurationError):
        C5ExperimentConfig(
            experiment_id="unsafe",
            environment="non_production",
            experiment_mode=True,
            database_url="postgresql://prod/database",
        ).validate()

    def no_network(*args, **kwargs):
        pytest.fail("CSV import attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    db = store(tmp_path)
    result = import_csv_vacancies(csv_bytes(row()), cfg, db)
    assert result.accepted == 1
    assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_csv_store_cannot_reuse_configured_application_sqlite_database(
    tmp_path, monkeypatch
) -> None:
    application_db = tmp_path / "application.db"
    database_url = f"sqlite+aiosqlite:///{application_db}"
    monkeypatch.setattr(
        "backend.ingestion.csv_import.get_settings",
        lambda: Settings(DATABASE_URL=database_url),
    )
    cfg = C5ExperimentConfig(
        experiment_id="csv-isolation",
        environment="non_production",
        experiment_mode=True,
        database_url=f"sqlite:///{application_db}",
    )
    db = C5ExperimentStore(tmp_path / "separate.db")
    with pytest.raises(CsvVacancyImportError, match="isolated"):
        preview_csv_vacancies(csv_bytes(row()), cfg, db)


def test_supplied_store_must_match_isolated_configured_path(tmp_path) -> None:
    cfg = config(tmp_path)
    db = C5ExperimentStore(tmp_path / "different.db")
    with pytest.raises(CsvVacancyImportError, match="configured isolated"):
        preview_csv_vacancies(csv_bytes(row()), cfg, db)


def test_import_limits_settings_are_validated() -> None:
    with pytest.raises(ValueError):
        Settings(CSV_IMPORT_MAX_ROWS=0)
    assert Settings(CSV_IMPORT_MAX_ROWS=500).CSV_IMPORT_MAX_ROWS == 500
