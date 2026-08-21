"""Tests for the AI Career Navigator and Skill Gap Analysis Engine.

Endpoints covered
-----------------
POST /api/v1/career/analyze   — Run AI career analysis pipeline
GET  /api/v1/career/latest    — Return latest saved insight
GET  /api/v1/career/history   — Paginated insight history

Strategy
--------
- ``backend.ai.career_navigator.CareerNavigatorService.navigate`` is patched
  with an ``AsyncMock`` returning a deterministic AI response. No Gemini key
  required.
- The database is the shared SQLite test database from ``conftest.py``; the
  full ORM stack and repository layer are exercised.
- A resume is uploaded (with mocked AI) so each test user has a parsed
  ``Resume.content`` available to the career service.
- Auth follows the same pattern as ``test_resume.py`` and ``test_jobs.py``:
  register + login inside a module-scoped fixture.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

ANALYZE_URL = "/api/v1/career/analyze"
LATEST_URL = "/api/v1/career/latest"
HISTORY_URL = "/api/v1/career/history"

_PATCH_NAVIGATE = "backend.services.career_service.CareerNavigatorService"
_PATCH_REAL_NAVIGATE = "backend.ai.career_navigator.CareerNavigatorService.navigate"
_PATCH_EXTRACT = "backend.services.resume_service.extract_text"
_PATCH_ANALYZE_CV = "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv"

_PDF_MIME = "application/pdf"

_MOCK_CV_RESULT: dict[str, Any] = {
    "name": "Taylor Engineer",
    "career_level": "Mid-level",
    "years_experience": "5 years",
    "skills": ["Python", "FastAPI", "Docker"],
    "technical_skills": ["Python", "FastAPI", "Docker", "PostgreSQL"],
    "soft_skills": ["Problem solving"],
    "industries": ["Software"],
    "strengths": ["Backend systems"],
    "skill_gaps": ["Kubernetes", "System design"],
    "recommended_roles": ["Senior Backend Engineer"],
}

_STUB_RESUME_TEXT = "Taylor Engineer 5 years Python FastAPI Docker"

_MOCK_NAVIGATE_RESULT: dict[str, Any] = {
    "career_direction": "Progress toward Senior Backend Engineer and then Staff Engineer.",
    "recommended_roles": ["Senior Backend Engineer", "Platform Engineer"],
    "career_path": [
        {
            "stage": "Short-term (0-6 months)",
            "timeline": "0-6 months",
            "actions": ["Learn Kubernetes", "Contribute to open source"],
        },
        {
            "stage": "Medium-term (6-18 months)",
            "timeline": "6-18 months",
            "actions": ["Lead a backend project", "Mentor junior engineers"],
        },
    ],
    "skill_priorities": ["Kubernetes", "System design", "Distributed systems"],
    "certification_recommendations": ["CKA (Certified Kubernetes Administrator)"],
    "job_search_strategy": [
        "Target Series B+ startups",
        "Optimize LinkedIn profile",
    ],
    "career_advice": (
        "Focus on system design skills to move from mid-level to senior. "
        "Document your impact with metrics."
    ),
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
    email = f"career_{uuid.uuid4().hex[:8]}@example.com"
    password = "careerrocks9"
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Career Tester"},
    )
    assert reg.status_code == 201, reg.text
    user_id = uuid.UUID(reg.json()["id"])
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"], user_id


def _upload_resume(client: TestClient, token: str) -> None:
    with (
        patch(_PATCH_EXTRACT, return_value=_STUB_RESUME_TEXT),
        patch(_PATCH_ANALYZE_CV, new=AsyncMock(return_value=_MOCK_CV_RESULT)),
    ):
        resp = client.post(
            "/api/v1/resume/upload",
            files={"file": ("cv.pdf", _make_pdf(), _PDF_MIME)},
            headers=_auth_header(token),
        )
    assert resp.status_code == 201, resp.text


# ------------------------------------------------------------------ #
# Module-scoped fixtures                                               #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def auth_with_resume(client: TestClient):
    """Register a user, upload a resume, return (token, user_id)."""
    token, user_id = _register_and_login(client)
    _upload_resume(client, token)
    return token, user_id


# ------------------------------------------------------------------ #
# Authentication guard tests                                           #
# ------------------------------------------------------------------ #

def test_analyze_requires_authentication(client: TestClient):
    """POST /career/analyze without a token returns 401."""
    resp = client.post(ANALYZE_URL, json={})
    assert resp.status_code == 401


def test_latest_requires_authentication(client: TestClient):
    """GET /career/latest without a token returns 401."""
    resp = client.get(LATEST_URL)
    assert resp.status_code == 401


def test_history_requires_authentication(client: TestClient):
    """GET /career/history without a token returns 401."""
    resp = client.get(HISTORY_URL)
    assert resp.status_code == 401


# ------------------------------------------------------------------ #
# Analyze — happy path                                                 #
# ------------------------------------------------------------------ #

def test_analyze_returns_201_with_structured_response(
    client: TestClient, auth_with_resume
):
    """POST /career/analyze returns 201 with the full AI analysis."""
    token, _ = auth_with_resume

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        resp = client.post(
            ANALYZE_URL,
            json={"target_role": "Senior Backend Engineer"},
            headers=_auth_header(token),
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["career_direction"] == _MOCK_NAVIGATE_RESULT["career_direction"]
    assert body["recommended_roles"] == _MOCK_NAVIGATE_RESULT["recommended_roles"]
    assert body["career_advice"] == _MOCK_NAVIGATE_RESULT["career_advice"]
    assert body["target_role"] == "Senior Backend Engineer"


def test_analyze_response_shape(client: TestClient, auth_with_resume):
    """Analyze response contains all expected top-level fields."""
    token, _ = auth_with_resume

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        resp = client.post(ANALYZE_URL, json={}, headers=_auth_header(token))

    assert resp.status_code == 201
    body = resp.json()
    for field in (
        "insight_id",
        "resume_id",
        "target_role",
        "created_at",
        "career_direction",
        "recommended_roles",
        "career_path",
        "skill_priorities",
        "certification_recommendations",
        "job_search_strategy",
        "career_advice",
    ):
        assert field in body, f"missing field: {field}"


def test_analyze_career_path_shape(client: TestClient, auth_with_resume):
    """career_path items each have stage, timeline, and actions."""
    token, _ = auth_with_resume

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        resp = client.post(ANALYZE_URL, json={}, headers=_auth_header(token))

    path = resp.json()["career_path"]
    assert isinstance(path, list)
    assert len(path) == 2
    for stage in path:
        assert "stage" in stage
        assert "timeline" in stage
        assert "actions" in stage
        assert isinstance(stage["actions"], list)


def test_analyze_without_target_role(client: TestClient, auth_with_resume):
    """POST /career/analyze with no target_role still succeeds."""
    token, _ = auth_with_resume

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        resp = client.post(ANALYZE_URL, json={}, headers=_auth_header(token))

    assert resp.status_code == 201
    assert resp.json()["target_role"] is None


def test_analyze_with_target_role(client: TestClient, auth_with_resume):
    """POST /career/analyze with target_role stores it in the insight."""
    token, _ = auth_with_resume

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        resp = client.post(
            ANALYZE_URL,
            json={"target_role": "Staff Engineer"},
            headers=_auth_header(token),
        )

    assert resp.status_code == 201
    assert resp.json()["target_role"] == "Staff Engineer"


# ------------------------------------------------------------------ #
# Analyze — missing resume                                             #
# ------------------------------------------------------------------ #

def test_analyze_without_resume_returns_404(client: TestClient):
    """POST /career/analyze for a user with no resume returns 404."""
    token, _ = _register_and_login(client)
    resp = client.post(ANALYZE_URL, json={}, headers=_auth_header(token))
    assert resp.status_code == 404
    assert "resume" in resp.json()["detail"].lower()


# ------------------------------------------------------------------ #
# Analyze — AI failure                                                 #
# ------------------------------------------------------------------ #

def test_analyze_ai_failure_returns_502(client: TestClient, auth_with_resume):
    """When the AI navigator raises, the endpoint returns 502."""
    from backend.ai.career_navigator import CareerNavigatorError

    token, _ = auth_with_resume

    with patch(
        _PATCH_REAL_NAVIGATE,
        new=AsyncMock(side_effect=CareerNavigatorError("Gemini down")),
    ):
        resp = client.post(ANALYZE_URL, json={}, headers=_auth_header(token))

    assert resp.status_code == 502


# ------------------------------------------------------------------ #
# Persistence — latest and history                                     #
# ------------------------------------------------------------------ #

def test_analyze_persists_and_latest_returns_it(client: TestClient, auth_with_resume):
    """Running an analysis makes it retrievable via GET /career/latest."""
    token, _ = auth_with_resume

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        client.post(ANALYZE_URL, json={}, headers=_auth_header(token))

    resp = client.get(LATEST_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "analysis" in body
    assert "created_at" in body


def test_latest_returns_404_when_no_analysis_exists(client: TestClient):
    """GET /career/latest returns 404 for a user who has never run an analysis."""
    token, _ = _register_and_login(client)
    resp = client.get(LATEST_URL, headers=_auth_header(token))
    assert resp.status_code == 404


def test_history_returns_paginated_response(client: TestClient, auth_with_resume):
    """GET /career/history returns a paginated envelope."""
    token, _ = auth_with_resume

    # Ensure at least one insight exists.
    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        client.post(ANALYZE_URL, json={}, headers=_auth_header(token))

    resp = client.get(HISTORY_URL, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert "offset" in body
    assert "limit" in body
    assert isinstance(body["items"], list)
    assert body["total"] >= 1


def test_history_pagination_params(client: TestClient, auth_with_resume):
    """Pagination params are honoured in the history response."""
    token, _ = auth_with_resume
    resp = client.get(HISTORY_URL + "?offset=0&limit=1", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["offset"] == 0
    assert body["limit"] == 1
    assert len(body["items"]) <= 1


def test_history_isolation_between_users(client: TestClient):
    """User A's insights are not visible to User B."""
    token_b, _ = _register_and_login(client)
    resp = client.get(HISTORY_URL, headers=_auth_header(token_b))
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_multiple_analyses_stack_in_history(client: TestClient, auth_with_resume):
    """Running analysis twice creates two history records."""
    token, _ = auth_with_resume

    before = client.get(HISTORY_URL, headers=_auth_header(token)).json()["total"]

    with patch(_PATCH_REAL_NAVIGATE, new=AsyncMock(return_value=_MOCK_NAVIGATE_RESULT)):
        client.post(ANALYZE_URL, json={}, headers=_auth_header(token))
        client.post(ANALYZE_URL, json={"target_role": "CTO"}, headers=_auth_header(token))

    after = client.get(HISTORY_URL, headers=_auth_header(token)).json()["total"]
    assert after == before + 2
