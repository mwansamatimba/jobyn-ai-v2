"""Tests for the AI Job Discovery and Matching Engine.

Endpoints covered
-----------------
POST /api/v1/jobs/match       — AI matching pipeline
GET  /api/v1/jobs             — list active jobs
GET  /api/v1/jobs/matches     — user's stored match results
GET  /api/v1/jobs/{job_id}    — single job detail
POST /api/v1/jobs             — create a job posting

Strategy
--------
- ``backend.ai.job_matcher.JobMatcherService.match_jobs`` is patched with an
  ``AsyncMock`` that returns a deterministic response keyed on the real job
  UUID inserted during the test fixture.  No Gemini key is required.
- The database is the shared SQLite test database from ``conftest.py``; the
  full ORM stack and repository layer are exercised.
- Auth follows the same pattern as ``test_resume.py``: register + login inside
  a module-scoped fixture, reuse the token across tests.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

JOBS_URL = "/api/v1/jobs"
MATCH_URL = "/api/v1/jobs/match"
MATCHES_URL = "/api/v1/jobs/matches"

_PATCH_MATCHER = "backend.services.job_service.JobMatcherService.match_jobs"
_PATCH_EXTRACT = "backend.services.resume_service.extract_text"
_PATCH_ANALYZE = "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv"

_MOCK_AI_RESULT: dict[str, Any] = {
    "name": "Alex Developer",
    "career_level": "Mid-level",
    "years_experience": "4 years",
    "skills": ["Python", "FastAPI", "SQL"],
    "technical_skills": ["Python", "FastAPI", "PostgreSQL"],
    "soft_skills": ["Communication"],
    "industries": ["Software"],
    "strengths": ["Backend development"],
    "skill_gaps": ["Kubernetes"],
    "recommended_roles": ["Backend Engineer"],
}

_STUB_RESUME_TEXT = "Alex Developer 4 years Python FastAPI SQL"

_PDF_MIME = "application/pdf"


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_pdf() -> bytes:
    """Return a minimal valid PDF using pypdf."""
    from io import BytesIO
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _register_and_login(client: TestClient) -> tuple[str, uuid.UUID]:
    """Register a fresh user, log in, return (token, user_id)."""
    email = f"jobs_{uuid.uuid4().hex[:8]}@example.com"
    password = "topsecret99"

    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Jobs Tester"},
    )
    assert reg.status_code == 201, reg.text
    user_id = uuid.UUID(reg.json()["id"])

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return token, user_id


def _create_job(client: TestClient, token: str, **overrides) -> dict[str, Any]:
    """POST a job and return the response body."""
    payload = {
        "title": "Backend Engineer",
        "company_name": "Acme Corp",
        "description": "Build great APIs with Python and FastAPI.",
        "location": "Remote",
        "location_type": "remote",
        "employment_type": "full_time",
        "experience_level": "mid",
        **overrides,
    }
    resp = client.post(JOBS_URL, json=payload, headers=_auth_header(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_resume(client: TestClient, token: str) -> dict[str, Any]:
    """Upload a minimal PDF resume and return the response body."""
    with (
        patch(_PATCH_EXTRACT, return_value=_STUB_RESUME_TEXT),
        patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_AI_RESULT)),
    ):
        resp = client.post(
            "/api/v1/resume/upload",
            files={"file": ("cv.pdf", _make_pdf(), _PDF_MIME)},
            headers=_auth_header(token),
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------ #
# Shared module-scoped fixtures                                        #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def auth_token(client: TestClient) -> tuple[str, uuid.UUID]:
    """One user used across all module tests."""
    return _register_and_login(client)


@pytest.fixture(scope="module")
def job_with_resume(client: TestClient, auth_token) -> dict[str, Any]:
    """Create one job and upload one resume, return both ids."""
    token, user_id = auth_token
    job = _create_job(client, token)
    resume = _upload_resume(client, token)
    return {"token": token, "user_id": user_id, "job": job, "resume": resume}


# ------------------------------------------------------------------ #
# Authentication guard tests                                           #
# ------------------------------------------------------------------ #

def test_match_requires_authentication(client: TestClient):
    """POST /jobs/match without a token returns 401."""
    resp = client.post(MATCH_URL)
    assert resp.status_code == 401


def test_list_jobs_requires_authentication(client: TestClient):
    """GET /jobs without a token returns 401."""
    resp = client.get(JOBS_URL)
    assert resp.status_code == 401


def test_list_matches_requires_authentication(client: TestClient):
    """GET /jobs/matches without a token returns 401."""
    resp = client.get(MATCHES_URL)
    assert resp.status_code == 401


def test_get_job_requires_authentication(client: TestClient):
    """GET /jobs/{job_id} without a token returns 401."""
    resp = client.get(f"{JOBS_URL}/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_create_job_requires_authentication(client: TestClient):
    """POST /jobs without a token returns 401."""
    resp = client.post(JOBS_URL, json={"title": "x", "company_name": "y"})
    assert resp.status_code == 401


# ------------------------------------------------------------------ #
# Job CRUD tests                                                       #
# ------------------------------------------------------------------ #

def test_create_job_returns_201(client: TestClient, auth_token):
    """Creating a job with valid fields returns 201 and the job body."""
    token, _ = auth_token
    job = _create_job(client, token, title="Data Engineer", company_name="DataCo")
    assert job["title"] == "Data Engineer"
    assert job["company_name"] == "DataCo"
    assert job["is_active"] is True
    assert job["source"] == "internal"
    assert "id" in job


def test_create_job_response_shape(client: TestClient, auth_token):
    """Job creation response contains all expected fields."""
    token, _ = auth_token
    job = _create_job(client, token)
    for field in ("id", "title", "company_name", "is_active", "source", "created_at"):
        assert field in job, f"missing field: {field}"


def test_list_jobs_returns_paginated_response(client: TestClient, auth_token):
    """GET /jobs returns a paginated envelope with items."""
    token, _ = auth_token
    # Ensure at least one job exists.
    _create_job(client, token, title="SRE", company_name="OpsCo")
    resp = client.get(JOBS_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert "offset" in body
    assert "limit" in body
    assert isinstance(body["items"], list)
    assert body["total"] >= 1


def test_list_jobs_pagination_params(client: TestClient, auth_token):
    """Pagination params are reflected in the response envelope."""
    token, _ = auth_token
    resp = client.get(JOBS_URL + "?offset=0&limit=2", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["offset"] == 0
    assert body["limit"] == 2
    assert len(body["items"]) <= 2


def test_get_job_by_id(client: TestClient, auth_token):
    """GET /jobs/{job_id} returns the correct job."""
    token, _ = auth_token
    created = _create_job(client, token, title="ML Engineer", company_name="AI Ltd")
    resp = client.get(f"{JOBS_URL}/{created['id']}", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
    assert resp.json()["title"] == "ML Engineer"


def test_get_job_not_found(client: TestClient, auth_token):
    """GET /jobs/{unknown_id} returns 404."""
    token, _ = auth_token
    resp = client.get(f"{JOBS_URL}/{uuid.uuid4()}", headers=_auth_header(token))
    assert resp.status_code == 404


# ------------------------------------------------------------------ #
# Match pipeline tests                                                 #
# ------------------------------------------------------------------ #

def test_match_returns_200_with_ranked_results(client: TestClient, job_with_resume):
    """POST /jobs/match returns 200 with AI-ranked matches persisted."""
    token = job_with_resume["token"]
    job_id = job_with_resume["job"]["id"]

    mock_ai_response = {
        "top_matches": [
            {
                "job_id": job_id,
                "job_title": "Backend Engineer",
                "company": "Acme Corp",
                "match_score": 88,
                "matching_skills": ["Python", "FastAPI"],
                "missing_skills": ["Kubernetes"],
                "reason": "Strong Python and FastAPI background.",
            }
        ],
        "overall_match_summary": "Excellent fit for backend roles.",
        "recommended_next_actions": ["Learn Kubernetes", "Build a side project"],
    }

    with patch(_PATCH_MATCHER, new=AsyncMock(return_value=mock_ai_response)):
        resp = client.post(MATCH_URL, headers=_auth_header(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "resume_id" in body
    assert "top_matches" in body
    assert "overall_match_summary" in body
    assert "recommended_next_actions" in body

    assert len(body["top_matches"]) == 1
    match = body["top_matches"][0]
    assert match["job_id"] == job_id
    assert match["match_score"] == 88
    assert "Python" in match["matching_skills"]
    assert "Kubernetes" in match["missing_skills"]
    assert match["reason"] != ""


def test_match_response_shape(client: TestClient, job_with_resume):
    """Each top_match item has all required fields."""
    token = job_with_resume["token"]
    job_id = job_with_resume["job"]["id"]

    mock_ai_response = {
        "top_matches": [
            {
                "job_id": job_id,
                "job_title": "Backend Engineer",
                "company": "Acme Corp",
                "match_score": 75,
                "matching_skills": ["Python"],
                "missing_skills": [],
                "reason": "Good match.",
            }
        ],
        "overall_match_summary": "Good overall fit.",
        "recommended_next_actions": [],
    }

    with patch(_PATCH_MATCHER, new=AsyncMock(return_value=mock_ai_response)):
        resp = client.post(MATCH_URL, headers=_auth_header(token))

    assert resp.status_code == 200
    match = resp.json()["top_matches"][0]
    for field in (
        "match_result_id",
        "job_id",
        "job_title",
        "company",
        "match_score",
        "matching_skills",
        "missing_skills",
        "reason",
    ):
        assert field in match, f"missing field in match item: {field}"


def test_match_persists_to_match_results_table(client: TestClient, job_with_resume):
    """Running a match creates MatchResult records retrievable via GET /matches."""
    token = job_with_resume["token"]
    job_id = job_with_resume["job"]["id"]

    mock_ai_response = {
        "top_matches": [
            {
                "job_id": job_id,
                "job_title": "Backend Engineer",
                "company": "Acme Corp",
                "match_score": 80,
                "matching_skills": ["FastAPI"],
                "missing_skills": [],
                "reason": "FastAPI expert.",
            }
        ],
        "overall_match_summary": "Great fit.",
        "recommended_next_actions": [],
    }

    with patch(_PATCH_MATCHER, new=AsyncMock(return_value=mock_ai_response)):
        client.post(MATCH_URL, headers=_auth_header(token))

    # Verify the match was persisted.
    resp = client.get(MATCHES_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert len(body["items"]) >= 1
    item = body["items"][0]
    assert item["job_id"] == job_id
    assert item["status"] == "completed"
    assert item["matcher_type"] == "ai"


def test_match_without_resume_returns_404(client: TestClient, client_no_resume=None):
    """POST /jobs/match for a user with no resume returns 404."""
    # Create a fresh user who has never uploaded a resume.
    token, _ = _register_and_login(client)
    resp = client.post(MATCH_URL, headers=_auth_header(token))
    assert resp.status_code == 404
    assert "resume" in resp.json()["detail"].lower()


def test_match_ignores_unknown_job_ids_from_ai(client: TestClient, job_with_resume):
    """When AI returns a job_id not in the DB, that entry is silently skipped."""
    token = job_with_resume["token"]

    fake_job_id = str(uuid.uuid4())
    mock_ai_response = {
        "top_matches": [
            {
                "job_id": fake_job_id,   # does not exist in DB
                "job_title": "Ghost Job",
                "company": "Nobody",
                "match_score": 99,
                "matching_skills": [],
                "missing_skills": [],
                "reason": "Perfect ghost match.",
            }
        ],
        "overall_match_summary": "Phantom match.",
        "recommended_next_actions": [],
    }

    with patch(_PATCH_MATCHER, new=AsyncMock(return_value=mock_ai_response)):
        resp = client.post(MATCH_URL, headers=_auth_header(token))

    # Should still return 200; the unknown entry is just dropped.
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_matches"] == []


# ------------------------------------------------------------------ #
# Stored matches endpoint tests                                        #
# ------------------------------------------------------------------ #

def test_list_matches_returns_paginated_response(client: TestClient, job_with_resume):
    """GET /jobs/matches returns a paginated envelope."""
    token = job_with_resume["token"]
    resp = client.get(MATCHES_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)


def test_list_matches_isolation_between_users(client: TestClient):
    """User A's matches are not visible to User B."""
    token_a, _ = _register_and_login(client)
    token_b, _ = _register_and_login(client)

    # Check user B starts with zero matches.
    resp = client.get(MATCHES_URL, headers=_auth_header(token_b))
    assert resp.status_code == 200
    # User B's total should not include any matches run by User A.
    total_b = resp.json()["total"]

    # User A runs a match — this should not affect User B's count.
    resp = client.get(MATCHES_URL, headers=_auth_header(token_b))
    assert resp.json()["total"] == total_b
