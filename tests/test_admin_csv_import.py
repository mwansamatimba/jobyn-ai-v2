from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from pathlib import Path

import pytest
from backend.core.config import get_settings
from backend.core.security import create_access_token
from backend.database.session import async_session_factory
from backend.ingestion.store import C5ExperimentStore
from backend.models.user import User
from sqlalchemy import select

PREVIEW = "/api/v1/admin/jobs/csv/preview"
IMPORT = "/api/v1/admin/jobs/csv/import"
HISTORY = "/api/v1/admin/jobs/csv/imports"


def csv_content(rows: list[dict[str, str]] | None = None, *, count: int = 0) -> bytes:
    fields = [
        "title",
        "company",
        "location",
        "description",
        "source_name",
        "source_url",
        "application_url",
        "external_id",
        "country",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in rows or []:
        writer.writerow(item)
    for index in range(count):
        writer.writerow(vacancy(f"job-{index}"))
    return output.getvalue().encode("utf-8")


def vacancy(external_id: str = "job-1", **overrides: str) -> dict[str, str]:
    data = {
        "title": "Software Engineer",
        "company": "Example Zambia Ltd",
        "location": "Lusaka, Zambia",
        "description": "Build useful services.",
        "source_name": "go_zambia_jobs",
        "source_url": f"https://gozambiajobs.com/jobs/{external_id}",
        "application_url": f"https://careers.example.zm/apply/{external_id}",
        "external_id": external_id,
        "country": "Zambia",
    }
    return {**data, **overrides}


def set_c5_env(monkeypatch, tmp_path, experiment_id: str = "admin-csv-test") -> str:
    path = tmp_path / "isolated-c5.sqlite"
    monkeypatch.setenv("C5_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("C5_ENVIRONMENT", "non_production")
    monkeypatch.setenv("C5_EXPERIMENT_MODE", "true")
    monkeypatch.setenv("C5_RETRIEVAL_METHOD", "csv_import")
    monkeypatch.setenv("C5_DATABASE_URL", f"sqlite:///{path}")
    return str(path)


def _legacy_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def login(client, role: str | None = None) -> tuple[str, str]:
    email = f"csv-{uuid.uuid4()}@example.com"
    password = "not-a-real-production-password"
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "CSV Test"},
    )
    assert registered.status_code == 201, registered.text
    if role:
        async def promote() -> None:
            async with async_session_factory() as session:
                user = (
                    await session.execute(select(User).where(User.email == email))
                ).scalar_one()
                user.role = role
                await session.commit()

        asyncio.run(promote())
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return email, response.json()["access_token"]


def upload(
    client,
    path: str,
    content: bytes,
    *,
    filename: str = "jobs.csv",
    content_type: str = "text/csv",
    headers=None,
    confirmation_token: str = "invalid-preview-token",
):
    data = {"confirmation_token": confirmation_token} if path == IMPORT else None
    return client.post(
        path,
        files={"file": (filename, content, content_type)},
        data=data,
        headers=headers or {},
    )


def preview_token(client, content: bytes, headers, *, mapping=None) -> str:
    data = {"column_mapping": json.dumps(mapping)} if mapping is not None else None
    response = client.post(
        PREVIEW,
        files={"file": ("jobs.csv", content, "text/csv")},
        data=data,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["confirmation_token"]


def confirmed_import(client, content: bytes, headers):
    return upload(
        client,
        IMPORT,
        content,
        headers=headers,
        confirmation_token=preview_token(client, content, headers),
    )


@pytest.fixture
def client_and_admin(client, monkeypatch, tmp_path):
    c5_path = set_c5_env(monkeypatch, tmp_path)
    _, token = login(client, "admin")
    return client, auth_headers(token), c5_path


def test_anonymous_preview_and_import_require_authentication(client, monkeypatch, tmp_path):
    set_c5_env(monkeypatch, tmp_path)
    content = csv_content([vacancy()])
    assert upload(client, PREVIEW, content).status_code == 401
    assert upload(client, IMPORT, content).status_code == 401
    assert client.get(HISTORY).status_code == 401


def test_authenticated_non_admin_is_forbidden(client, monkeypatch, tmp_path):
    set_c5_env(monkeypatch, tmp_path)
    _, token = login(client)
    headers = auth_headers(token)
    content = csv_content([vacancy()])
    assert upload(client, PREVIEW, content, headers=headers).status_code == 403
    assert upload(client, IMPORT, content, headers=headers).status_code == 403
    assert client.get(HISTORY, headers=headers).status_code == 403


def test_admin_role_claim_is_ignored_and_missing_user_is_rejected(client):
    email, user_token = login(client)

    async def get_user_id():
        async with async_session_factory() as session:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one()
            return user.id

    forged_headers = auth_headers(
        create_access_token(
            str(asyncio.run(get_user_id())),
            extra_claims={"role": "admin"},
        )
    )
    assert client.get(HISTORY, headers=forged_headers).status_code == 403
    missing_user_headers = auth_headers(create_access_token(str(uuid.uuid4())))
    assert client.get(HISTORY, headers=missing_user_headers).status_code == 401
    assert client.get(HISTORY, headers=auth_headers(user_token)).status_code == 403


def test_inactive_admin_is_forbidden(client, monkeypatch, tmp_path):
    set_c5_env(monkeypatch, tmp_path)
    email, token = login(client, "admin")

    async def deactivate() -> None:
        async with async_session_factory() as session:
            user = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one()
            user.is_active = False
            await session.commit()

    asyncio.run(deactivate())
    response = upload(
        client,
        PREVIEW,
        csv_content([vacancy()]),
        headers=auth_headers(token),
    )
    assert response.status_code == 403


def test_admin_preview_has_zero_persistent_writes(client_and_admin):
    client, headers, db_path = client_and_admin
    assert client.get("/admin").status_code == 200
    content = csv_content([vacancy()])
    response = upload(client, PREVIEW, content, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "jobs.csv"
    assert body["rows_detected"] == 1
    assert body["valid_rows"] == 1
    assert body["invalid_rows"] == 0
    assert body["ready_to_import"] == 1
    assert body["records"] == [
        {
            "row": 2,
            "title": "Software Engineer",
            "company": "Example Zambia Ltd",
            "location": "Lusaka, Zambia",
            "source_name": "go_zambia_jobs",
            "source_url_domain": "gozambiajobs.com",
            "application_url_present": True,
            "existing_action": "new",
        }
    ]
    assert body["confirmation_token"]
    assert not Path(db_path).exists()


def test_admin_preview_does_not_modify_existing_staging_database(client_and_admin):
    client, headers, db_path = client_and_admin
    store = C5ExperimentStore(Path(db_path))
    store.close()
    before = Path(db_path).read_bytes()

    response = upload(
        client,
        PREVIEW,
        csv_content([vacancy()]),
        headers=headers,
    )
    assert response.status_code == 200
    assert Path(db_path).read_bytes() == before


def test_admin_import_uses_c5_store_and_history_endpoints(client_and_admin):
    client, headers, db_path = client_and_admin
    content = csv_content([vacancy()])
    response = confirmed_import(client, content, headers)
    assert response.status_code == 200
    body = response.json()
    assert body["rows_submitted"] == 1
    assert body["rows_valid"] == 1
    assert body["jobs_created"] == 1
    assert body["status"] == "completed"

    store = C5ExperimentStore.open_readonly(Path(db_path))
    assert store.count_jobs() == 1
    assert store.count_source("go_zambia_jobs") == 1
    assert store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    link = store.connection.execute(
        "SELECT source_url,application_url,attribution FROM source_links"
    ).fetchone()
    assert tuple(link) == (
        "https://gozambiajobs.com/jobs/job-1",
        "https://careers.example.zm/apply/job-1",
        "Source: go_zambia_jobs",
    )
    store.close()

    before_history = Path(db_path).read_bytes()
    history = client.get(HISTORY, headers=headers)
    assert history.status_code == 200
    item = history.json()[0]
    assert item["batch_id"] == body["batch_id"]
    assert item["filename"] == "jobs.csv"
    assert item["imported_count"] == 1
    detail = client.get(f"{HISTORY}/{body['batch_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["sources"] == [
        {"source_name": "go_zambia_jobs", "observations": 1}
    ]
    assert detail.json()["errors_retained"] is False
    assert client.get(f"{HISTORY}/missing-batch", headers=headers).status_code == 404
    assert Path(db_path).read_bytes() == before_history


def test_import_rejects_file_or_mapping_changed_after_preview(client_and_admin):
    client, headers, db_path = client_and_admin
    original = csv_content([vacancy()])
    token = preview_token(client, original, headers)
    changed_file = csv_content([vacancy(title="Different reviewed title")])
    response = upload(
        client,
        IMPORT,
        changed_file,
        headers=headers,
        confirmation_token=token,
    )
    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]
    assert not Path(db_path).exists()

    renamed = upload(
        client,
        IMPORT,
        original,
        filename="different-name.csv",
        headers=headers,
        confirmation_token=token,
    )
    assert renamed.status_code == 409
    assert not Path(db_path).exists()

    mapping = {"position": "title"}
    mapped_content = (
        b"position,company,location,description,source_name,source_url,application_url\n"
        b"Engineer,Example,Lusaka Zambia,Details,go_zambia_jobs,"
        b"https://gozambiajobs.com/job,https://example.zm/apply\n"
    )
    mapped_token = preview_token(client, mapped_content, headers, mapping=mapping)
    mismatched = client.post(
        IMPORT,
        files={"file": ("jobs.csv", mapped_content, "text/csv")},
        data={"confirmation_token": mapped_token},
        headers=headers,
    )
    assert mismatched.status_code == 409
    assert not Path(db_path).exists()


def test_replaying_confirmation_token_is_idempotent(client_and_admin):
    client, headers, db_path = client_and_admin
    content = csv_content([vacancy()])
    token = preview_token(client, content, headers)

    first = upload(
        client,
        IMPORT,
        content,
        headers=headers,
        confirmation_token=token,
    )
    replay = upload(
        client,
        IMPORT,
        content,
        headers=headers,
        confirmation_token=token,
    )

    assert first.status_code == replay.status_code == 200
    assert replay.json()["batch_id"] == first.json()["batch_id"]
    store = C5ExperimentStore.open_readonly(Path(db_path))
    assert store.count_jobs() == 1
    assert store.count_source("go_zambia_jobs") == 1
    assert store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM csv_import_batches").fetchone()[0] == 1
    store.close()


def test_repeat_upload_and_changed_record_use_existing_importer(client_and_admin):
    client, headers, _ = client_and_admin
    first = confirmed_import(client, csv_content([vacancy()]), headers)
    assert first.json()["jobs_created"] == 1
    repeat = confirmed_import(client, csv_content([vacancy()]), headers)
    assert repeat.json()["jobs_created"] == 0
    assert repeat.json()["duplicates_skipped"] == 1
    changed = vacancy(description="Updated description")
    updated = confirmed_import(client, csv_content([changed]), headers)
    assert updated.json()["jobs_updated"] == 1


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("jobs.txt", "text/plain", 400),
        ("jobs.csv", "application/pdf", 400),
        ("empty.csv", "text/csv", 400),
    ],
)
def test_upload_type_filename_and_empty_checks(
    client_and_admin, filename: str, content_type: str, expected: int
):
    client, headers, _ = client_and_admin
    response = upload(
        client,
        PREVIEW,
        b"" if filename == "empty.csv" else csv_content([vacancy()]),
        filename=filename,
        content_type=content_type,
        headers=headers,
    )
    assert response.status_code == expected


def test_preview_errors_invalid_urls_mapping_and_personal_data(client_and_admin):
    client, headers, _ = client_and_admin
    invalid = vacancy(application_url="javascript:alert(1)")
    response = upload(client, PREVIEW, csv_content([invalid]), headers=headers)
    assert response.status_code == 200
    error = response.json()["errors"][0]
    assert error["row"] == 2
    assert error["field"] == "application_url"

    unfamiliar = (
        b"Position,Employer,Place,Details,Source,Listing,Apply\n"
        b'"Engineer","Example","Kitwe, Zambia","Details","go_zambia_jobs",'
        b'"https://gozambiajobs.com/job","https://example.zm/apply"\n'
    )
    mapping = {
        "Position": "title",
        "Employer": "company",
        "Place": "location",
        "Details": "description",
        "Source": "source_name",
        "Listing": "source_url",
        "Apply": "application_url",
    }
    mapped = client.post(
        PREVIEW,
        files={"file": ("jobs.csv", unfamiliar, "text/csv")},
        data={"column_mapping": json.dumps(mapping)},
        headers=headers,
    )
    assert mapped.status_code == 200
    assert mapped.json()["ready_to_import"] == 1

    personal = (
        b"title,company,location,description,source_name,source_url,application_url,"
        b"candidate_email\n"
        b"Engineer,Company,Lusaka Zambia,Details,go_zambia_jobs,"
        b"https://gozambiajobs.com/job,https://example.zm/apply,secret@example.test\n"
    )
    assert upload(client, PREVIEW, personal, headers=headers).status_code == 400


def test_row_limit_is_enforced_at_api(client_and_admin):
    client, headers, _ = client_and_admin
    content = csv_content(count=501)
    response = upload(client, PREVIEW, content, headers=headers)
    assert response.status_code == 400
    assert "row limit" in response.json()["detail"]


def test_file_size_limit_is_enforced_at_api(client_and_admin):
    client, headers, _ = client_and_admin
    content = csv_content([vacancy()]) + b"x" * (5 * 1024 * 1024)
    response = upload(client, PREVIEW, content, headers=headers)
    assert response.status_code == 400
    assert "5 MiB" in response.json()["detail"]


def test_request_body_limit_rejects_oversized_multipart_before_parsing(client_and_admin):
    client, headers, _ = client_and_admin
    content = csv_content([vacancy()]) + b"x" * (6 * 1024 * 1024)
    response = upload(client, PREVIEW, content, headers=headers)
    assert response.status_code == 413


def test_c5_storage_configuration_failure_and_production_db_rejection(
    client, monkeypatch, tmp_path
):
    _, token = login(client, "admin")
    headers = auth_headers(token)
    content = csv_content([vacancy()])
    monkeypatch.delenv("C5_DATABASE_URL", raising=False)
    assert upload(client, PREVIEW, content, headers=headers).status_code == 503
    app_db_url = get_settings().DATABASE_URL
    monkeypatch.setenv("C5_EXPERIMENT_ID", "unsafe-same-db")
    monkeypatch.setenv("C5_ENVIRONMENT", "non_production")
    monkeypatch.setenv("C5_EXPERIMENT_MODE", "true")
    monkeypatch.setenv("C5_DATABASE_URL", app_db_url.replace("+aiosqlite", ""))
    assert upload(client, IMPORT, content, headers=headers).status_code == 503
