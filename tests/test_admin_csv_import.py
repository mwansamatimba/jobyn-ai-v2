"""Administrator CSV job import API tests."""

import csv
import io

import pytest

from backend.core.config import settings

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
PREVIEW = "/api/v1/admin/jobs/import/preview"
IMPORT = "/api/v1/admin/jobs/import"
TEMPLATE = "/api/v1/admin/jobs/import/template"
HISTORY = "/api/v1/admin/jobs/import/history"


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_and_login(client, email: str) -> str:
    assert client.post(
        REGISTER,
        json={"email": email, "password": "supersecret1", "full_name": "CSV Admin"},
    ).status_code == 201
    response = client.post(LOGIN, json={"email": email, "password": "supersecret1"})
    assert response.status_code == 200
    return response.json()["access_token"]


def csv_bytes(rows, headers=None) -> bytes:
    headers = headers or [
        "title", "company", "description", "location", "province", "country",
        "employment_type", "experience_level", "category", "remote_eligibility",
        "date_posted", "deadline", "external_url", "source", "external_id",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def job_row(external_id="csv-001", external_url="https://example.com/jobs/csv-001"):
    return {
        "title": "CSV Software Engineer",
        "company": "CSV Company",
        "description": "<p>Python and SQL.</p><script>blocked</script>",
        "location": "Lusaka, Zambia",
        "province": "Lusaka Province",
        "country": "Zambia",
        "employment_type": "full_time",
        "experience_level": "mid",
        "category": "ict_technology",
        "remote_eligibility": "not_remote",
        "date_posted": "2026-09-20",
        "deadline": "2026-10-20",
        "external_url": external_url,
        "source": "external",
        "external_id": external_id,
    }


@pytest.fixture
def admin_token(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["admin-csv@example.com"])
    return register_and_login(client, "admin-csv@example.com")


def test_admin_endpoints_require_authentication(client):
    body = csv_bytes([job_row()])
    assert client.post(PREVIEW, files={"file": ("jobs.csv", body, "text/csv")}).status_code == 401
    assert client.post(IMPORT, files={"file": ("jobs.csv", body, "text/csv")}).status_code == 401
    assert client.get(TEMPLATE).status_code == 401
    assert client.get(HISTORY).status_code == 401


def test_normal_user_is_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["other@example.com"])
    token = register_and_login(client, "normal-csv@example.com")
    body = csv_bytes([job_row()])
    assert client.post(
        PREVIEW, files={"file": ("jobs.csv", body, "text/csv")}, headers=auth_header(token)
    ).status_code == 403


def test_admin_preview_and_import(client, admin_token):
    body = csv_bytes([job_row()])
    headers = auth_header(admin_token)

    preview = client.post(PREVIEW, files={"file": ("vacancies.csv", body, "text/csv")}, headers=headers)
    assert preview.status_code == 200
    assert preview.json()["total_rows"] == 1
    assert preview.json()["valid_rows"] == 1
    assert preview.json()["invalid_rows"] == 0
    assert preview.json()["ready_to_import"] == 1

    imported = client.post(IMPORT, files={"file": ("vacancies.csv", body, "text/csv")}, headers=headers)
    assert imported.status_code == 200
    assert imported.json()["rows_imported"] == 1

    jobs = client.get("/api/v1/jobs", params={"source": "admin_csv", "limit": 100}, headers=headers)
    assert jobs.status_code == 200
    job = next(item for item in jobs.json()["items"] if item["title"] == "CSV Software Engineer")
    assert job["source_name"] == "admin_csv"
    assert job["date_posted"].startswith("2026-09-20")
    assert "<script>" not in (job["description"] or "")


def test_imported_job_flows_through_search_filters_and_detail(client, admin_token):
    headers = auth_header(admin_token)
    body = csv_bytes([job_row("csv-api-001", "https://example.com/jobs/csv-api-001")])
    assert client.post(IMPORT, files={"file": ("jobs.csv", body, "text/csv")}, headers=headers).status_code == 200

    search = client.get(
        "/api/v1/jobs/search",
        params={"q": "CSV Software Engineer", "province": "Lusaka Province", "limit": 100},
        headers=headers,
    )
    assert search.status_code == 200
    item = search.json()["items"][0]

    filters = client.get("/api/v1/jobs/filters", headers=headers)
    assert filters.status_code == 200
    assert "admin_csv" in filters.json()["sources"]

    detail = client.get(f"/api/v1/jobs/{item['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == item["id"]


def test_reupload_is_idempotent(client, admin_token):
    headers = auth_header(admin_token)
    body = csv_bytes([job_row("csv-idempotent", "https://example.com/jobs/idempotent")])
    first = client.post(IMPORT, files={"file": ("jobs.csv", body, "text/csv")}, headers=headers)
    second = client.post(IMPORT, files={"file": ("jobs.csv", body, "text/csv")}, headers=headers)
    assert first.json()["rows_imported"] == 1
    assert second.json()["rows_imported"] == 0
    assert second.json()["rows_skipped_duplicates"] == 1


def test_same_application_url_is_deduplicated(client, admin_token):
    headers = auth_header(admin_token)
    first = csv_bytes([job_row("url-a", "https://example.com/jobs/same?utm_source=test")])
    second = csv_bytes([job_row("url-b", "https://example.com/jobs/same")])
    assert client.post(IMPORT, files={"file": ("a.csv", first, "text/csv")}, headers=headers).json()["rows_imported"] == 1
    result = client.post(IMPORT, files={"file": ("b.csv", second, "text/csv")}, headers=headers)
    assert result.json()["rows_imported"] == 0
    assert result.json()["rows_skipped_duplicates"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("employment_type", "invalid"),
        ("experience_level", "invalid"),
        ("category", "invalid"),
        ("remote_eligibility", "invalid"),
        ("source", "invalid"),
        ("date_posted", "not-a-date"),
        ("deadline", "not-a-date"),
        ("external_url", "javascript:blocked"),
    ],
)
def test_invalid_values_are_reported(client, admin_token, field, value):
    row = job_row(f"bad-{field}", f"https://example.com/jobs/bad-{field}")
    row[field] = value
    response = client.post(
        PREVIEW, files={"file": ("bad.csv", csv_bytes([row]), "text/csv")},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["invalid_rows"] == 1
    assert response.json()["ready_to_import"] == 0


def test_missing_required_column_is_rejected(client, admin_token):
    body = csv_bytes([{"title": "Missing URL", "company": "Company"}], ["title", "company"])
    response = client.post(
        PREVIEW, files={"file": ("missing.csv", body, "text/csv")},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 400
    assert "external_url" in response.json()["detail"]


def test_mixed_valid_and_invalid_rows(client, admin_token):
    good = job_row("mixed-good", "https://example.com/jobs/mixed-good")
    bad = job_row("mixed-bad", "javascript:blocked")
    body = csv_bytes([good, bad])
    headers = auth_header(admin_token)

    preview = client.post(PREVIEW, files={"file": ("mixed.csv", body, "text/csv")}, headers=headers)
    assert preview.json()["valid_rows"] == 1
    assert preview.json()["invalid_rows"] == 1

    imported = client.post(IMPORT, files={"file": ("mixed.csv", body, "text/csv")}, headers=headers)
    assert imported.json()["rows_imported"] == 1
    assert imported.json()["rows_rejected"] == 1


def test_utf8_extra_columns_and_blank_rows(client, admin_token):
    row = job_row("utf8", "https://example.com/jobs/utf8")
    row["title"] = "Développeur logiciel — Lusaka"
    headers = list(row) + ["extra_column"]
    body = csv_bytes([row], headers)
    response = client.post(IMPORT, files={"file": ("utf8.csv", body, "text/csv")}, headers=auth_header(admin_token))
    assert response.status_code == 200
    assert response.json()["rows_imported"] == 1


def test_malformed_csv_is_rejected(client, admin_token):
    body = b'title,company,external_url\n"Unclosed,Company,https://example.com/job\n'
    response = client.post(
        PREVIEW, files={"file": ("broken.csv", body, "text/csv")}, headers=auth_header(admin_token)
    )
    assert response.status_code == 400


def test_empty_and_non_csv_uploads_are_rejected(client, admin_token):
    headers = auth_header(admin_token)
    empty = client.post(PREVIEW, files={"file": ("empty.csv", b"", "text/csv")}, headers=headers)
    assert empty.status_code == 400
    wrong = client.post(
        PREVIEW,
        files={"file": ("jobs.txt", b"title,company,external_url\nA,B,https://example.com/a\n", "text/plain")},
        headers=headers,
    )
    assert wrong.status_code == 400


def test_template_and_history(client, admin_token):
    headers = auth_header(admin_token)
    template = client.get(TEMPLATE, headers=headers)
    assert template.status_code == 200
    assert b"title,company" in template.content

    body = csv_bytes([job_row("history-001", "https://example.com/jobs/history-001")])
    assert client.post(IMPORT, files={"file": ("history.csv", body, "text/csv")}, headers=headers).status_code == 200
    history = client.get(HISTORY, headers=headers)
    assert history.status_code == 200
    item = next(row for row in history.json() if row["filename"] == "history.csv")
    assert item["administrator"] == "admin-csv@example.com"
    assert item["imported_rows"] == 1


def test_admin_page_and_date_posted_alias(client, admin_token):
    page = client.get("/admin")
    assert page.status_code == 200
    assert b"Confirm import" in page.content

    body = csv_bytes([job_row("alias-001", "https://example.com/jobs/alias-001")])
    assert client.post(IMPORT, files={"file": ("alias.csv", body, "text/csv")}, headers=auth_header(admin_token)).status_code == 200
    jobs = client.get("/api/v1/jobs", params={"source": "admin_csv", "limit": 100}, headers=auth_header(admin_token))
    item = next(job for job in jobs.json()["items"] if job["external_url"] == "https://example.com/jobs/alias-001")
    assert "date_posted" in item
    assert "posted_at" not in item


def test_upload_size_limit(client, admin_token):
    body = b"title,company,external_url\nA,B,https://example.com/a\n" + b"x" * (5 * 1024 * 1024)
    response = client.post(PREVIEW, files={"file": ("large.csv", body, "text/csv")}, headers=auth_header(admin_token))
    assert response.status_code == 400


def test_row_limit(client, admin_token):
    rows = [job_row(f"bulk-{i}", f"https://example.com/jobs/bulk-{i}") for i in range(1001)]
    response = client.post(
        PREVIEW, files={"file": ("too-many.csv", csv_bytes(rows), "text/csv")},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 400


def test_same_external_id_from_other_source_does_not_merge(client, admin_token):
    import asyncio
    import uuid
    from backend.database.session import async_session_factory
    from backend.ingestion.dedup import make_canonical_key
    from backend.models.enums import JobSource, PermissionStatus, SourceType
    from backend.models.ingestion import IngestionSource, JobIngestionSource
    from backend.models.job import Job

    external_id = "shared-source-id"
    canonical = make_canonical_key("greenhouse", external_id, "https://greenhouse.example/jobs/shared")

    async def seed():
        async with async_session_factory() as session:
            source = IngestionSource(
                id=uuid.uuid4(), name="greenhouse-isolation", organization_name="Greenhouse",
                source_type=SourceType.API, permission_status=PermissionStatus.OFFICIAL_API, active=True,
            )
            job = Job(
                id=uuid.uuid4(), title="Greenhouse Job", company_name="Greenhouse Co",
                external_id=external_id, source_name="greenhouse-isolation", canonical_key=canonical,
                external_url="https://greenhouse.example/jobs/shared", source=JobSource.EXTERNAL,
                is_active=True,
            )
            session.add_all([source, job])
            await session.flush()
            session.add(JobIngestionSource(
                id=uuid.uuid4(), job_id=job.id, source_id=source.id, external_id=external_id,
                application_url=job.external_url, status="active",
            ))
            await session.commit()

    asyncio.run(seed())
    body = csv_bytes([job_row(external_id, "https://example.com/jobs/shared-admin")])
    response = client.post(IMPORT, files={"file": ("jobs.csv", body, "text/csv")}, headers=auth_header(admin_token))
    assert response.status_code == 200
    assert response.json()["rows_imported"] == 1


def test_fatal_import_error_rolls_back_batch(client, admin_token, monkeypatch):
    from backend.database.session import async_session_factory
    from backend.models.job import Job
    from sqlalchemy import select

    async def fail(*args, **kwargs):
        raise RuntimeError("forced database failure")

    monkeypatch.setattr("backend.services.admin_csv_import._process_job", fail)
    external_id = "rollback-001"
    body = csv_bytes([job_row(external_id, "https://example.com/jobs/rollback-001")])
    response = client.post(IMPORT, files={"file": ("rollback.csv", body, "text/csv")}, headers=auth_header(admin_token))
    assert response.status_code == 500

    async def check():
        async with async_session_factory() as session:
            result = await session.execute(select(Job).where(Job.external_id == external_id))
            return result.scalar_one_or_none()

    assert asyncio.run(check()) is None
