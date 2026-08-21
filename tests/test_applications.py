"""Tests for the Application Tracking Engine.

Endpoints covered
-----------------
POST   /api/v1/applications                     Create application
GET    /api/v1/applications                     List applications
GET    /api/v1/applications/{id}                Get one application
PATCH  /api/v1/applications/{id}                Update application
DELETE /api/v1/applications/{id}                Delete application

Strategy
--------
- No AI calls are made — no mocking of Gemini needed here.
- The shared SQLite test database from conftest.py is used.
- Jobs are created via the existing POST /api/v1/jobs endpoint.
- Auth follows the register + login pattern from all other test modules.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from io import BytesIO
from pypdf import PdfWriter

# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

APPS_URL = "/api/v1/applications"
JOBS_URL = "/api/v1/jobs"

_PATCH_EXTRACT = "backend.services.resume_service.extract_text"
_PATCH_ANALYZE_CV = "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv"
_PDF_MIME = "application/pdf"

_MOCK_CV_RESULT: dict[str, Any] = {
    "name": "App Tester",
    "career_level": "Mid-level",
    "years_experience": "3 years",
    "skills": ["Python"],
    "technical_skills": ["Python"],
    "soft_skills": [],
    "industries": ["Software"],
    "strengths": [],
    "skill_gaps": [],
    "recommended_roles": ["Backend Engineer"],
}


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _register_and_login(client: TestClient) -> tuple[str, uuid.UUID]:
    email = f"apps_{uuid.uuid4().hex[:8]}@example.com"
    password = "apptracker9"
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "App Tester"},
    )
    assert reg.status_code == 201, reg.text
    user_id = uuid.UUID(reg.json()["id"])
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"], user_id


def _create_job(client: TestClient, token: str, **overrides) -> dict[str, Any]:
    payload = {
        "title": "Backend Engineer",
        "company_name": "Test Corp",
        "description": "Python backend role.",
        "location": "Remote",
        **overrides,
    }
    resp = client.post(JOBS_URL, json=payload, headers=_auth_header(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_resume(client: TestClient, token: str) -> None:
    with (
        patch(_PATCH_EXTRACT, return_value="App Tester 3 years Python"),
        patch(_PATCH_ANALYZE_CV, new=AsyncMock(return_value=_MOCK_CV_RESULT)),
    ):
        resp = client.post(
            "/api/v1/resume/upload",
            files={"file": ("cv.pdf", _make_pdf(), _PDF_MIME)},
            headers=_auth_header(token),
        )
    assert resp.status_code == 201, resp.text


def _create_app(
    client: TestClient,
    token: str,
    job_id: str,
    **overrides,
) -> dict[str, Any]:
    payload = {"job_id": job_id, **overrides}
    resp = client.post(APPS_URL, json=payload, headers=_auth_header(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------ #
# Module-scoped fixtures                                               #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def setup(client: TestClient) -> dict[str, Any]:
    """Register a user, create a job. Return token, user_id, job."""
    token, user_id = _register_and_login(client)
    job = _create_job(client, token)
    return {"token": token, "user_id": user_id, "job": job}


# ------------------------------------------------------------------ #
# Authentication guard tests                                           #
# ------------------------------------------------------------------ #

def test_create_requires_authentication(client: TestClient):
    resp = client.post(APPS_URL, json={"job_id": str(uuid.uuid4())})
    assert resp.status_code == 401


def test_list_requires_authentication(client: TestClient):
    resp = client.get(APPS_URL)
    assert resp.status_code == 401


def test_get_requires_authentication(client: TestClient):
    resp = client.get(f"{APPS_URL}/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_update_requires_authentication(client: TestClient):
    resp = client.patch(f"{APPS_URL}/{uuid.uuid4()}", json={"status": "applied"})
    assert resp.status_code == 401


def test_delete_requires_authentication(client: TestClient):
    resp = client.delete(f"{APPS_URL}/{uuid.uuid4()}")
    assert resp.status_code == 401


# ------------------------------------------------------------------ #
# Create — validation                                                  #
# ------------------------------------------------------------------ #

def test_create_nonexistent_job_returns_404(client: TestClient, setup):
    token = setup["token"]
    resp = client.post(
        APPS_URL,
        json={"job_id": str(uuid.uuid4())},
        headers=_auth_header(token),
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_create_invalid_status_returns_422(client: TestClient, setup):
    token = setup["token"]
    job_id = setup["job"]["id"]
    resp = client.post(
        APPS_URL,
        json={"job_id": job_id, "status": "dancing"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422


def test_create_invalid_initial_status_returns_422(client: TestClient, setup):
    """Cannot create an application directly with status 'offered'."""
    token = setup["token"]
    # Create a new job so this test is independent of the module fixture job.
    job = _create_job(client, token, title="Unique Job for Status Test")
    resp = client.post(
        APPS_URL,
        json={"job_id": job["id"], "status": "offered"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------ #
# Create — happy path                                                  #
# ------------------------------------------------------------------ #

def test_create_application_returns_201(client: TestClient, setup):
    token = setup["token"]
    job_id = setup["job"]["id"]
    app = _create_app(client, token, job_id)
    assert app["job_id"] == job_id
    assert app["user_id"] == str(setup["user_id"])
    assert app["status"] == "draft"
    assert "id" in app


def test_create_application_response_shape(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Shape Test Job")
    app = _create_app(client, token, job["id"])
    for field in (
        "id", "user_id", "job_id", "resume_id", "status",
        "cover_letter", "notes", "applied_at", "created_at", "updated_at",
    ):
        assert field in app, f"missing field: {field}"


def test_create_with_cover_letter(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Cover Letter Job")
    cover = "Dear Hiring Manager, I am an excellent candidate."
    app = _create_app(client, token, job["id"], cover_letter=cover)
    assert app["cover_letter"] == cover


def test_create_with_notes(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Notes Job")
    app = _create_app(client, token, job["id"], notes="Referred by Alice")
    assert app["notes"] == "Referred by Alice"


def test_create_with_status_applied(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Applied Status Job")
    app = _create_app(client, token, job["id"], status="applied")
    assert app["status"] == "applied"
    # applied_at should be set automatically
    assert app["applied_at"] is not None


def test_create_default_status_is_draft(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Default Status Job")
    app = _create_app(client, token, job["id"])
    assert app["status"] == "draft"


def test_create_duplicate_application_returns_409(client: TestClient, setup):
    """Creating a second application for the same job returns 409."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Duplicate Test Job")
    _create_app(client, token, job["id"])
    # Second create for same job.
    resp = client.post(
        APPS_URL,
        json={"job_id": job["id"]},
        headers=_auth_header(token),
    )
    assert resp.status_code == 409


def test_create_correct_user_association(client: TestClient):
    token, user_id = _register_and_login(client)
    job = _create_job(client, token, title="User Association Job")
    app = _create_app(client, token, job["id"])
    assert app["user_id"] == str(user_id)


# ------------------------------------------------------------------ #
# Cover letter integration — copilot output can be submitted          #
# ------------------------------------------------------------------ #

def test_create_application_with_copilot_cover_letter(client: TestClient):
    """A cover letter from the Application Copilot can be stored on creation."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Copilot Integration Job")
    cover = (
        "Dear Hiring Manager,\n\nI am applying for the Backend Engineer role. "
        "My Python and FastAPI skills directly match your requirements.\n\n"
        "Sincerely, App Tester"
    )
    app = _create_app(client, token, job["id"], cover_letter=cover)
    assert app["status"] == "draft"
    assert app["cover_letter"] == cover


# ------------------------------------------------------------------ #
# Retrieval                                                            #
# ------------------------------------------------------------------ #

def test_get_own_application(client: TestClient, setup):
    token = setup["token"]
    # Create a dedicated job so this test is independent of other tests
    # that may have already created an application for the fixture job.
    job = _create_job(client, token, title="Get Own Application Job")
    created = _create_app(client, token, job["id"])
    resp = client.get(
        f"{APPS_URL}/{created['id']}",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_unknown_application_returns_404(client: TestClient, setup):
    token = setup["token"]
    resp = client.get(
        f"{APPS_URL}/{uuid.uuid4()}",
        headers=_auth_header(token),
    )
    assert resp.status_code == 404


def test_cannot_get_another_users_application(client: TestClient):
    """User B cannot retrieve User A's application."""
    token_a, _ = _register_and_login(client)
    token_b, _ = _register_and_login(client)
    job = _create_job(client, token_a, title="Cross-User Get Job")
    app = _create_app(client, token_a, job["id"])

    resp = client.get(
        f"{APPS_URL}/{app['id']}",
        headers=_auth_header(token_b),
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------ #
# Listing                                                              #
# ------------------------------------------------------------------ #

def test_list_returns_only_own_applications(client: TestClient):
    token_a, _ = _register_and_login(client)
    token_b, _ = _register_and_login(client)
    job_a = _create_job(client, token_a, title="List Isolation Job A")
    job_b = _create_job(client, token_b, title="List Isolation Job B")
    _create_app(client, token_a, job_a["id"])
    _create_app(client, token_b, job_b["id"])

    resp_a = client.get(APPS_URL, headers=_auth_header(token_a))
    resp_b = client.get(APPS_URL, headers=_auth_header(token_b))

    ids_a = {item["id"] for item in resp_a.json()["items"]}
    ids_b = {item["id"] for item in resp_b.json()["items"]}
    assert ids_a.isdisjoint(ids_b), "User A and User B share application records"


def test_list_returns_paginated_response(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Pagination Job")
    _create_app(client, token, job["id"])
    resp = client.get(APPS_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    for field in ("items", "total", "offset", "limit"):
        assert field in body, f"missing field: {field}"
    assert isinstance(body["items"], list)
    assert body["total"] >= 1


def test_list_pagination_params_respected(client: TestClient):
    token, _ = _register_and_login(client)
    resp = client.get(APPS_URL + "?offset=0&limit=2", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["offset"] == 0
    assert body["limit"] == 2
    assert len(body["items"]) <= 2


def test_list_status_filter(client: TestClient):
    token, _ = _register_and_login(client)
    job_draft = _create_job(client, token, title="Status Filter Draft Job")
    job_applied = _create_job(client, token, title="Status Filter Applied Job")
    _create_app(client, token, job_draft["id"])
    _create_app(client, token, job_applied["id"], status="applied")

    resp = client.get(APPS_URL + "?status=applied", headers=_auth_header(token))
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["status"] == "applied"


def test_list_job_filter(client: TestClient):
    token, _ = _register_and_login(client)
    job1 = _create_job(client, token, title="Job Filter Job 1")
    job2 = _create_job(client, token, title="Job Filter Job 2")
    app1 = _create_app(client, token, job1["id"])
    _create_app(client, token, job2["id"])

    resp = client.get(
        f"{APPS_URL}?job_id={job1['id']}",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert app1["id"] in ids
    for item in resp.json()["items"]:
        assert item["job_id"] == job1["id"]


# ------------------------------------------------------------------ #
# Update — permitted fields                                            #
# ------------------------------------------------------------------ #

def test_update_notes(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Notes Update Job")
    app = _create_app(client, token, job["id"])
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"notes": "Updated notes"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Updated notes"


def test_update_cover_letter(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Cover Letter Update Job")
    app = _create_app(client, token, job["id"])
    new_cover = "Updated cover letter text."
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"cover_letter": new_cover},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["cover_letter"] == new_cover


def test_update_status_valid_transition(client: TestClient):
    """draft → applied is a valid transition."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Valid Transition Job")
    app = _create_app(client, token, job["id"])  # starts as draft
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"status": "applied"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"


def test_update_status_applied_stamps_applied_at(client: TestClient):
    """Transitioning to 'applied' auto-stamps applied_at."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Applied At Stamp Job")
    app = _create_app(client, token, job["id"])
    assert app["applied_at"] is None  # draft has no applied_at
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"status": "applied"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["applied_at"] is not None


def test_update_status_invalid_transition_returns_422(client: TestClient):
    """draft → interviewing skips steps and should be rejected."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Invalid Transition Job")
    app = _create_app(client, token, job["id"])  # draft
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"status": "interviewing"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422


def test_update_status_invalid_value_returns_422(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Bad Status Job")
    app = _create_app(client, token, job["id"])
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"status": "promoted"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422


def test_cannot_update_another_users_application(client: TestClient):
    token_a, _ = _register_and_login(client)
    token_b, _ = _register_and_login(client)
    job = _create_job(client, token_a, title="Cross-User Update Job")
    app = _create_app(client, token_a, job["id"])
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"notes": "Hacked"},
        headers=_auth_header(token_b),
    )
    assert resp.status_code == 404


def test_update_unknown_application_returns_404(client: TestClient):
    token, _ = _register_and_login(client)
    resp = client.patch(
        f"{APPS_URL}/{uuid.uuid4()}",
        json={"status": "applied"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------ #
# Full lifecycle                                                       #
# ------------------------------------------------------------------ #

def test_full_lifecycle_draft_to_offered(client: TestClient):
    """Walk through draft → applied → under_review → interviewing → offered."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Full Lifecycle Job")
    app = _create_app(client, token, job["id"])
    app_id = app["id"]

    for new_status in ("applied", "under_review", "interviewing", "offered"):
        resp = client.patch(
            f"{APPS_URL}/{app_id}",
            json={"status": new_status},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200, f"Failed at transition to {new_status}: {resp.text}"
        assert resp.json()["status"] == new_status


def test_terminal_status_offered_cannot_transition(client: TestClient):
    """offered is terminal — no further updates allowed."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Terminal Offered Job")
    app = _create_app(client, token, job["id"])
    app_id = app["id"]

    for s in ("applied", "under_review", "interviewing", "offered"):
        client.patch(f"{APPS_URL}/{app_id}", json={"status": s}, headers=_auth_header(token))

    # Now try to transition away from offered.
    resp = client.patch(
        f"{APPS_URL}/{app_id}",
        json={"status": "under_review"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422


def test_withdrawn_is_reachable_from_any_active_status(client: TestClient):
    """draft → withdrawn is always allowed."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Withdraw Job")
    app = _create_app(client, token, job["id"])
    resp = client.patch(
        f"{APPS_URL}/{app['id']}",
        json={"status": "withdrawn"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "withdrawn"


# ------------------------------------------------------------------ #
# Delete                                                               #
# ------------------------------------------------------------------ #

def test_delete_own_application(client: TestClient):
    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="Delete Test Job")
    app = _create_app(client, token, job["id"])
    resp = client.delete(
        f"{APPS_URL}/{app['id']}",
        headers=_auth_header(token),
    )
    assert resp.status_code == 204

    # Confirm it's gone.
    get_resp = client.get(
        f"{APPS_URL}/{app['id']}",
        headers=_auth_header(token),
    )
    assert get_resp.status_code == 404


def test_delete_unknown_application_returns_404(client: TestClient):
    token, _ = _register_and_login(client)
    resp = client.delete(
        f"{APPS_URL}/{uuid.uuid4()}",
        headers=_auth_header(token),
    )
    assert resp.status_code == 404


def test_cannot_delete_another_users_application(client: TestClient):
    token_a, _ = _register_and_login(client)
    token_b, _ = _register_and_login(client)
    job = _create_job(client, token_a, title="Cross-User Delete Job")
    app = _create_app(client, token_a, job["id"])
    resp = client.delete(
        f"{APPS_URL}/{app['id']}",
        headers=_auth_header(token_b),
    )
    assert resp.status_code == 404
    # Confirm it still exists for User A.
    get_resp = client.get(
        f"{APPS_URL}/{app['id']}",
        headers=_auth_header(token_a),
    )
    assert get_resp.status_code == 200
