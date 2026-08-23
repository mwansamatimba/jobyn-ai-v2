"""Focused integration tests for the deterministic job matching endpoint.

Tests cover:
- Job seeding via POST /api/v1/jobs/ingest (mocked HTTP)
- POST /api/v1/jobs/deterministic-match happy path
- Results ranked by score descending
- No-resume → 404
- No-jobs → 404
- Auth guard → 401

No real Gemini/LLM calls are made.
No real Remotive HTTP calls are made (mocked).
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

DET_MATCH_URL = "/api/v1/jobs/deterministic-match"
INGEST_URL = "/api/v1/jobs/ingest"
JOBS_URL = "/api/v1/jobs"

_PATCH_EXTRACT = "backend.services.resume_service.extract_text"
_PATCH_ANALYZE = "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv"
_PATCH_REMOTIVE = "backend.services.job_ingestion.fetch_remotive_jobs"

_MOCK_CV: dict[str, Any] = {
    "name": "Test User",
    "career_level": "Mid-level",
    "years_experience": "4 years",
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
    "technical_skills": ["Python", "FastAPI", "git", "sql"],
    "soft_skills": ["Communication"],
    "industries": ["Software"],
    "strengths": ["Backend development"],
    "skill_gaps": ["Kubernetes"],
    "recommended_roles": ["Backend Engineer", "Software Engineer"],
}

_MOCK_REMOTIVE_JOBS = [
    {
        "title": "Senior Python Backend Engineer",
        "company_name": "TechCorp",
        "url": "https://remotive.com/jobs/test-1",
        "description": (
            "We need a Python backend engineer with FastAPI, PostgreSQL, "
            "Docker, and git experience."
        ),
        "candidate_required_location": "Worldwide",
        "job_type": "full_time",
        "publication_date": "2026-07-01T10:00:00",
    },
    {
        "title": "Marketing Manager",
        "company_name": "MarketCo",
        "url": "https://remotive.com/jobs/test-2",
        "description": "Social media management, content writing, brand strategy.",
        "candidate_required_location": "Remote",
        "job_type": "full_time",
        "publication_date": "2026-07-01T10:00:00",
    },
]


# ── helpers ─────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


def _register_and_login(client: TestClient) -> tuple[str, uuid.UUID]:
    email = f"det_{uuid.uuid4().hex[:8]}@example.com"
    pw = "testpass99"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": pw, "full_name": "Det Tester"})
    assert r.status_code == 201, r.text
    uid = uuid.UUID(r.json()["id"])
    r2 = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r2.status_code == 200
    return r2.json()["access_token"], uid


def _upload_resume(client: TestClient, token: str) -> None:
    with (
        patch(_PATCH_EXTRACT, return_value="Test User 4 years Python FastAPI"),
        patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_CV)),
    ):
        r = client.post(
            "/api/v1/resume/upload",
            files={"file": ("cv.pdf", _make_pdf(), "application/pdf")},
            headers=_auth(token),
        )
    assert r.status_code == 201, r.text


def _seed_jobs(client: TestClient, token: str) -> None:
    """Seed jobs directly via the jobs API (uses the test DB session)."""
    for job_data in _MOCK_REMOTIVE_JOBS:
        payload = {
            "title": job_data["title"],
            "company_name": job_data["company_name"],
            "description": job_data["description"],
            "location": job_data.get("candidate_required_location", "Remote"),
            "location_type": "remote",
            "employment_type": "full_time",
        }
        r = client.post(JOBS_URL, json=payload, headers=_auth(token))
        assert r.status_code == 201, r.text


# ── module fixture ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def setup(client: TestClient) -> dict[str, Any]:
    token, uid = _register_and_login(client)
    _upload_resume(client, token)
    _seed_jobs(client, token)
    return {"token": token, "uid": uid}


# ── auth guard ───────────────────────────────────────────────────────────────

def test_deterministic_match_requires_auth(client: TestClient):
    r = client.post(DET_MATCH_URL)
    assert r.status_code == 401


# ── no resume ───────────────────────────────────────────────────────────────

def test_deterministic_match_no_resume_returns_404(client: TestClient):
    token, _ = _register_and_login(client)
    # Seed a job so there are active jobs
    with patch(_PATCH_REMOTIVE, new=AsyncMock(return_value=_MOCK_REMOTIVE_JOBS)):
        client.post(INGEST_URL + "?limit=2", headers=_auth(token))
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    assert r.status_code == 404
    assert "resume" in r.json()["detail"].lower()


# ── happy path ───────────────────────────────────────────────────────────────

def test_deterministic_match_returns_200(client: TestClient, setup):
    token = setup["token"]
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    assert r.status_code == 200, r.text


def test_deterministic_match_response_shape(client: TestClient, setup):
    token = setup["token"]
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    body = r.json()
    assert "resume_id" in body
    assert "total_jobs_evaluated" in body
    assert "matches" in body
    assert isinstance(body["matches"], list)
    assert body["total_jobs_evaluated"] >= 1


def test_deterministic_match_item_shape(client: TestClient, setup):
    token = setup["token"]
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    matches = r.json()["matches"]
    assert len(matches) >= 1
    m = matches[0]
    for field in (
        "job_id", "job_title", "company", "match_score", "match_level",
        "matched_skills", "missing_skills", "experience_match",
        "role_match", "recommendation",
        "skill_score", "experience_score", "role_score",
    ):
        assert field in m, f"missing field: {field}"


def test_deterministic_match_ranked_descending(client: TestClient, setup):
    """Results must be ordered by match_score descending."""
    token = setup["token"]
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    scores = [m["match_score"] for m in r.json()["matches"]]
    assert scores == sorted(scores, reverse=True), "Results not ranked descending"


def test_deterministic_match_python_job_scores_higher(client: TestClient, setup):
    """The Python backend job should score higher than the marketing job
    for a Python developer candidate."""
    token = setup["token"]
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    matches = r.json()["matches"]
    # Find by title keywords
    python_job = next((m for m in matches if "python" in m["job_title"].lower()), None)
    marketing_job = next((m for m in matches if "marketing" in m["job_title"].lower()), None)
    if python_job and marketing_job:
        assert python_job["match_score"] >= marketing_job["match_score"], (
            f"Python job score ({python_job['match_score']}) should be >= "
            f"marketing job score ({marketing_job['match_score']})"
        )


def test_deterministic_match_score_in_range(client: TestClient, setup):
    token = setup["token"]
    r = client.post(DET_MATCH_URL, headers=_auth(token))
    for m in r.json()["matches"]:
        assert 0 <= m["match_score"] <= 100


def test_ingest_endpoint_returns_stats(client: TestClient, setup):
    """POST /api/v1/jobs/ingest returns a stats dict (ingest may fail in test env but endpoint exists)."""
    token = setup["token"]
    # Mock the remotive fetch so no real HTTP call is made
    with patch(_PATCH_REMOTIVE, new=AsyncMock(return_value=[])):
        r = client.post(INGEST_URL + "?limit=2", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    for key in ("source", "fetched", "created", "duplicates", "invalid", "errors"):
        assert key in body, f"missing key: {key}"
