"""Focused regression test for non-mutating admin CSV preview."""

from backend.core.config import settings


def test_admin_csv_preview_does_not_insert_jobs(client, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["preview-admin@example.com"])

    email = "preview-admin@example.com"
    password = "supersecret1"
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Preview Admin"},
    )
    assert registration.status_code in {201, 409}

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    external_url = "https://example.com/jobs/preview-only"
    csv_body = (
        "title,company,description,location,external_url,external_id\n"
        f"Preview Job,Preview Company,Test,Lusaka,{external_url},preview-only\n"
    ).encode("utf-8")

    preview = client.post(
        "/api/v1/admin/jobs/import/preview",
        files={"file": ("preview.csv", csv_body, "text/csv")},
        headers=headers,
    )
    assert preview.status_code == 200
    assert preview.json()["ready_to_import"] == 1

    jobs = client.get(
        "/api/v1/jobs",
        params={"source": "admin_csv", "limit": 100},
        headers=headers,
    )
    assert jobs.status_code == 200
    assert all(item["external_url"] != external_url for item in jobs.json()["items"])
