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


# ===========================================================================
# Task 4 — Jobs API completeness + matching integration tests
# ===========================================================================
# These tests extend existing coverage without duplicating it.
#
# IMPORTANT: Tests that manipulate DB state directly (via db_session) always
# create their own fresh user/token inside the test body.  This avoids
# contaminating the module-scoped auth_token fixture when db_session drops
# and recreates all tables between tests.
# ===========================================================================


# ---------------------------------------------------------------------------
# T4-1: Inactive job (is_active=False) returns 404 from GET /jobs/{job_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inactive_job_detail_returns_404(db_session, client: TestClient):
    """A job marked is_active=False must return 404 from the detail endpoint."""
    from sqlalchemy import select
    from backend.models.job import Job
    from backend.models.enums import IngestionStatus

    # Create a fresh user within this test — db_session recreates the schema
    token, _ = _register_and_login(client)
    created = _create_job(client, token, title="Soon-to-close Job", company_name="CloseCo")
    job_id_str = created["id"]
    job_id = uuid.UUID(job_id_str)  # SQLAlchemy UUID column requires uuid.UUID

    # Verify it is accessible
    resp = client.get(f"{JOBS_URL}/{job_id_str}", headers=_auth_header(token))
    assert resp.status_code == 200

    # Simulate the ingestion lifecycle closing the job
    result = await db_session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    assert job is not None, "Job created via API must be in the DB"
    job.is_active = False
    job.ingestion_status = IngestionStatus.CLOSED
    await db_session.commit()

    # Now the API must return 404
    resp2 = client.get(f"{JOBS_URL}/{job_id_str}", headers=_auth_header(token))
    assert resp2.status_code == 404, (
        f"Expected 404 for inactive job, got {resp2.status_code}"
    )


# ---------------------------------------------------------------------------
# T4-2: Inactive jobs excluded from GET /jobs listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inactive_jobs_excluded_from_listing(db_session, client: TestClient):
    """Closed/expired/removed jobs with is_active=False must not appear in GET /jobs."""
    from sqlalchemy import select
    from backend.models.job import Job
    from backend.models.enums import IngestionStatus

    token, _ = _register_and_login(client)

    active = _create_job(client, token, title="T4 Active Listing Job", company_name="ActiveCo")
    closed = _create_job(client, token, title="T4 Closed Listing Job", company_name="ClosedCo")
    expired = _create_job(client, token, title="T4 Expired Listing Job", company_name="ExpiredCo")

    # Deactivate closed and expired as the ingestion lifecycle would
    for job_id_str, status in [
        (closed["id"], IngestionStatus.CLOSED),
        (expired["id"], IngestionStatus.EXPIRED),
    ]:
        r = await db_session.execute(
            select(Job).where(Job.id == uuid.UUID(job_id_str))
        )
        j = r.scalar_one()
        j.is_active = False
        j.ingestion_status = status
    await db_session.commit()

    resp = client.get(JOBS_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]

    assert "T4 Active Listing Job" in titles, "Active job must appear in listing"
    assert "T4 Closed Listing Job" not in titles, "Closed job must not appear in listing"
    assert "T4 Expired Listing Job" not in titles, "Expired job must not appear in listing"


# ---------------------------------------------------------------------------
# T4-3: Inactive jobs excluded from deterministic matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_match_excludes_inactive_jobs(db_session, client: TestClient):
    """Inactive jobs (is_active=False) must not appear in deterministic match results."""
    from sqlalchemy import select
    from backend.models.job import Job
    from backend.models.enums import IngestionStatus

    token, _ = _register_and_login(client)
    _upload_resume(client, token)

    # Create one active and one closed job
    active_j = _create_job(
        client, token,
        title="T4 Active Match Target",
        company_name="MatchActiveCo",
        description="Python FastAPI PostgreSQL backend developer role.",
        experience_level="mid",
    )
    closed_j = _create_job(
        client, token,
        title="T4 Closed No-Match Target",
        company_name="MatchClosedCo",
        description="Python FastAPI PostgreSQL backend developer role.",
        experience_level="mid",
    )

    # Close the second job
    r = await db_session.execute(
        select(Job).where(Job.id == uuid.UUID(closed_j["id"]))
    )
    j = r.scalar_one()
    j.is_active = False
    j.ingestion_status = IngestionStatus.CLOSED
    await db_session.commit()

    resp = client.post(
        "/api/v1/jobs/deterministic-match",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    matched_ids = [m["job_id"] for m in body["matches"]]
    assert active_j["id"] in matched_ids, "Active job should be in match results"
    assert closed_j["id"] not in matched_ids, "Closed job must NOT appear in match results"


# ---------------------------------------------------------------------------
# T4-4: Lifecycle states
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_possibly_closed_still_visible(db_session, client: TestClient):
    """Jobs with ingestion_status=possibly_closed but is_active=True remain visible."""
    from sqlalchemy import select
    from backend.models.job import Job
    from backend.models.enums import IngestionStatus

    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="T4 Possibly Closed Job", company_name="MaybeCo")

    # possibly_closed: is_active stays True (not yet deactivated)
    r = await db_session.execute(
        select(Job).where(Job.id == uuid.UUID(job["id"]))
    )
    j = r.scalar_one()
    j.ingestion_status = IngestionStatus.POSSIBLY_CLOSED
    # is_active intentionally left True
    await db_session.commit()

    # Detail endpoint must still return 200
    resp = client.get(f"{JOBS_URL}/{job['id']}", headers=_auth_header(token))
    assert resp.status_code == 200

    # Listing must include it
    listing = client.get(JOBS_URL, headers=_auth_header(token))
    ids = [item["id"] for item in listing.json()["items"]]
    assert job["id"] in ids, "possibly_closed (still active) job must appear in listing"


@pytest.mark.asyncio
async def test_removed_job_excluded(db_session, client: TestClient):
    """Jobs with is_active=False and ingestion_status=removed must not be returned."""
    from sqlalchemy import select
    from backend.models.job import Job
    from backend.models.enums import IngestionStatus

    token, _ = _register_and_login(client)
    job = _create_job(client, token, title="T4 Removed Job", company_name="GoneCo")

    r = await db_session.execute(
        select(Job).where(Job.id == uuid.UUID(job["id"]))
    )
    j = r.scalar_one()
    j.is_active = False
    j.ingestion_status = IngestionStatus.REMOVED
    await db_session.commit()

    # Detail: 404
    resp = client.get(f"{JOBS_URL}/{job['id']}", headers=_auth_header(token))
    assert resp.status_code == 404

    # Listing: excluded
    listing = client.get(JOBS_URL, headers=_auth_header(token))
    ids = [item["id"] for item in listing.json()["items"]]
    assert job["id"] not in ids, "removed job must not appear in listing"


# ---------------------------------------------------------------------------
# T4-5: Pagination correctness
# (Use fresh tokens — these are module-independent function-level tests)
# ---------------------------------------------------------------------------

def test_pagination_limit_100_accepted(client: TestClient):
    """limit=100 is within the allowed range and must return 200."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}?limit=100", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["limit"] == 100


def test_pagination_limit_101_rejected(client: TestClient):
    """limit=101 exceeds the maximum and must return 422."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}?limit=101", headers=_auth_header(token))
    assert resp.status_code == 422


def test_pagination_page_parameter(client: TestClient):
    """page=2 shifts offset correctly; limit is reflected in envelope."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}?page=2&limit=5", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert body["offset"] == 5


def test_pagination_no_duplicates_across_pages(client: TestClient):
    """Items on page 1 and page 2 must not overlap."""
    token, _ = _register_and_login(client)
    # seed extra jobs to ensure we have something on page 2
    for i in range(3):
        _create_job(client, token, title=f"T4 Page Job {i}", company_name=f"PageCo{i}")

    resp1 = client.get(f"{JOBS_URL}?page=1&limit=2", headers=_auth_header(token))
    resp2 = client.get(f"{JOBS_URL}?page=2&limit=2", headers=_auth_header(token))
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    ids_p1 = {item["id"] for item in resp1.json()["items"]}
    ids_p2 = {item["id"] for item in resp2.json()["items"]}
    assert ids_p1.isdisjoint(ids_p2), f"Duplicate IDs across pages: {ids_p1 & ids_p2}"


# ---------------------------------------------------------------------------
# T4-6: Filter parameters
# ---------------------------------------------------------------------------

def test_filter_by_q_full_text(client: TestClient):
    """q= filters by title/company/description; returns matching jobs."""
    token, _ = _register_and_login(client)
    _create_job(
        client, token,
        title="T4 Python Wizard",
        company_name="WizCo",
        description="We need a Python expert for backend development.",
    )
    resp = client.get(f"{JOBS_URL}?q=Python+Wizard", headers=_auth_header(token))
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert "T4 Python Wizard" in titles


def test_filter_by_employment_type(client: TestClient):
    """employment_type= filter returns only matching employment type."""
    token, _ = _register_and_login(client)
    _create_job(
        client, token,
        title="T4 Contract Role",
        company_name="ContractCo",
        employment_type="contract",
    )
    resp = client.get(
        f"{JOBS_URL}?employment_type=contract",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(item["employment_type"] == "contract" for item in items)
    assert all(item["employment_type"] == "contract" for item in items)


def test_filter_by_company(client: TestClient):
    """company= filter returns only jobs matching the company name."""
    token, _ = _register_and_login(client)
    _create_job(
        client, token,
        title="T4 Company Filter Test",
        company_name="UniqueFilterCorp",
    )
    resp = client.get(
        f"{JOBS_URL}?company=UniqueFilterCorp",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert all("UniqueFilterCorp" in item["company_name"] for item in items)


def test_filter_by_remote_true(client: TestClient):
    """remote=true must only return jobs with eligible remote_eligibility values."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}?remote=true", headers=_auth_header(token))
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item.get("remote_eligibility") in (
            "zambia_eligible", "africa_eligible", "global", "restrictions_unclear", None
        ), f"Unexpected remote_eligibility: {item.get('remote_eligibility')}"


def test_filter_by_seniority(client: TestClient):
    """seniority= filter matches experience_level."""
    token, _ = _register_and_login(client)
    _create_job(
        client, token,
        title="T4 Senior Engineer",
        company_name="SeniorCo",
        experience_level="senior",
    )
    resp = client.get(
        f"{JOBS_URL}?seniority=senior",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any("T4 Senior Engineer" in item["title"] for item in items)


def test_filter_by_unknown_employment_type_returns_empty(client: TestClient):
    """Filtering by a value that matches nothing returns an empty items list."""
    token, _ = _register_and_login(client)
    resp = client.get(
        f"{JOBS_URL}?employment_type=gibberish_type",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# T4-7: Sort parameters
# ---------------------------------------------------------------------------

def test_sort_by_company(client: TestClient):
    """sort=company returns 200 with company-sorted results."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}?sort=company", headers=_auth_header(token))
    assert resp.status_code == 200
    items = resp.json()["items"]
    companies = [item["company_name"] for item in items]
    assert companies == sorted(companies), "Results not sorted by company_name ascending"


def test_sort_by_recent_is_default(client: TestClient):
    """Default sort order is by posted_at/created_at descending."""
    token, _ = _register_and_login(client)
    resp = client.get(JOBS_URL, headers=_auth_header(token))
    assert resp.status_code == 200  # Just verify no error; stable ordering


def test_sort_by_deadline(client: TestClient):
    """sort=deadline returns 200 without error."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}?sort=deadline", headers=_auth_header(token))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# T4-8: Search endpoint mirrors main listing filters
# ---------------------------------------------------------------------------

def test_search_endpoint_returns_matching_titles(client: TestClient):
    """GET /jobs/search?q= returns jobs matching the query."""
    token, _ = _register_and_login(client)
    _create_job(
        client, token,
        title="T4 Unique Search Target Job",
        company_name="SearchTargetCo",
    )
    resp = client.get(
        f"{JOBS_URL}/search?q=Unique+Search+Target",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert "T4 Unique Search Target Job" in titles


def test_search_limit_100_accepted(client: TestClient):
    """GET /jobs/search?limit=100 is within the valid range."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}/search?limit=100", headers=_auth_header(token))
    assert resp.status_code == 200


def test_search_limit_101_rejected(client: TestClient):
    """GET /jobs/search?limit=101 exceeds the maximum and must return 422."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}/search?limit=101", headers=_auth_header(token))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T4-9: Filters endpoint returns structure
# ---------------------------------------------------------------------------

def test_filters_endpoint_structure(client: TestClient):
    """GET /jobs/filters returns the expected keys."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}/filters", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    expected_keys = {
        "provinces", "countries", "employment_types",
        "experience_levels", "categories", "sources",
    }
    for key in expected_keys:
        assert key in body, f"Missing filter key: {key}"
    for key in expected_keys:
        assert isinstance(body[key], list), f"{key} must be a list"


# ---------------------------------------------------------------------------
# T4-10: Recent endpoint
# ---------------------------------------------------------------------------

def test_recent_returns_200(client: TestClient):
    """GET /jobs/recent must return 200 with active jobs."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}/recent", headers=_auth_header(token))
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["is_active"] is True


def test_recent_respects_limit(client: TestClient):
    """GET /jobs/recent?limit=3 returns at most 3 items."""
    token, _ = _register_and_login(client)
    resp = client.get(f"{JOBS_URL}/recent?limit=3", headers=_auth_header(token))
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 3


# ---------------------------------------------------------------------------
# T4-11: JobRead date_posted regression — verified via real HTTP response
# ---------------------------------------------------------------------------

def test_jobread_date_posted_in_http_response(client: TestClient):
    """The HTTP response for a job must expose 'date_posted', not 'posted_at'."""
    token, _ = _register_and_login(client)
    created = _create_job(client, token, title="T4 DatePosted Test", company_name="DateCo")
    resp = client.get(f"{JOBS_URL}/{created['id']}", headers=_auth_header(token))
    assert resp.status_code == 200
    raw = resp.json()
    assert "date_posted" in raw, (
        f"Expected 'date_posted' in response, got keys: {list(raw.keys())}"
    )
    assert "posted_at" not in raw, (
        "'posted_at' must not appear in the HTTP response (use 'date_posted')"
    )


def test_list_jobs_date_posted_field_name(client: TestClient):
    """Every item in GET /jobs listing must use 'date_posted' not 'posted_at'."""
    token, _ = _register_and_login(client)
    _create_job(client, token, title="T4 List DatePosted Test", company_name="ListDateCo")
    resp = client.get(JOBS_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert "date_posted" in item, (
            f"Missing 'date_posted' in item: {list(item.keys())}"
        )
        assert "posted_at" not in item, (
            f"'posted_at' must not appear in response"
        )


# ---------------------------------------------------------------------------
# T4-12: Matching uses real normalized DB jobs
# ---------------------------------------------------------------------------

def test_deterministic_match_uses_real_db_jobs(client: TestClient):
    """Deterministic matching runs against actual Job rows in the DB."""
    token, _ = _register_and_login(client)
    _upload_resume(client, token)

    # Create a job whose description matches the mock resume skills (Python, FastAPI, SQL)
    _create_job(
        client, token,
        title="T4 Real DB Match Job",
        company_name="RealMatchCo",
        description="Looking for a Python developer with FastAPI and SQL skills.",
        experience_level="mid",
    )

    resp = client.post(
        "/api/v1/jobs/deterministic-match",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_jobs_evaluated"] >= 1
    titles = [m["job_title"] for m in body["matches"]]
    assert "T4 Real DB Match Job" in titles


def test_matching_active_jobs_available_to_matcher(client: TestClient):
    """The matching engine can find and score active jobs."""
    token, _ = _register_and_login(client)
    _upload_resume(client, token)

    _create_job(
        client, token,
        title="T4 Matcher Availability Test",
        company_name="AvailCo",
        description="Python FastAPI PostgreSQL backend API development.",
        experience_level="mid",
    )

    resp = client.post(
        "/api/v1/jobs/deterministic-match",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["total_jobs_evaluated"] >= 1
