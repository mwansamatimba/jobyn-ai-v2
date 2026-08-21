"""Tests for the Interview Preparation endpoint.

Endpoint: POST /api/v1/applications/{application_id}/interview-prep

Mocks InterviewCoachService.generate_interview_plan — no real AI calls.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

APPS_URL = "/api/v1/applications"
JOBS_URL = "/api/v1/jobs"

_PATCH_COACH = (
    "backend.services.interview_service.InterviewCoachService.generate_interview_plan"
)
_PATCH_EXTRACT = "backend.services.resume_service.extract_text"
_PATCH_ANALYZE_CV = "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv"
_PDF_MIME = "application/pdf"

_MOCK_CV = {
    "name": "Interview Tester",
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

_MOCK_PLAN: dict[str, Any] = {
    "target_role": "Backend Engineer",
    "readiness_score": 72,
    "interview_questions": [
        {
            "question": "Describe your Python experience.",
            "category": "Technical",
            "difficulty": "Medium",
            "ideal_answer_points": ["Mention FastAPI", "Mention async"],
        }
    ],
    "strength_areas": ["Python proficiency"],
    "improvement_areas": ["System design"],
    "preparation_plan": ["Review system design basics"],
    "final_advice": "Focus on system design to stand out.",
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
    email = f"iv_{uuid.uuid4().hex[:8]}@example.com"
    password = "interview99"
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "IV Tester"},
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
        patch(_PATCH_EXTRACT, return_value="Interview Tester 3 years Python"),
        patch(_PATCH_ANALYZE_CV, new=AsyncMock(return_value=_MOCK_CV)),
    ):
        resp = client.post(
            "/api/v1/resume/upload",
            files={"file": ("cv.pdf", _make_pdf(), _PDF_MIME)},
            headers=_auth_header(token),
        )
    assert resp.status_code == 201, resp.text


def _create_job(client: TestClient, token: str) -> dict[str, Any]:
    resp = client.post(
        JOBS_URL,
        json={"title": "Backend Engineer", "company_name": "TestCo"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_app(client: TestClient, token: str, job_id: str) -> dict[str, Any]:
    resp = client.post(
        APPS_URL,
        json={"job_id": job_id},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _interview_url(app_id: str) -> str:
    return f"{APPS_URL}/{app_id}/interview-prep"


# ------------------------------------------------------------------ #
# Module fixture                                                       #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def setup(client: TestClient) -> dict[str, Any]:
    token, user_id = _register_and_login(client)
    _upload_resume(client, token)
    job = _create_job(client, token)
    app = _create_app(client, token, job["id"])
    return {"token": token, "user_id": user_id, "job": job, "app": app}


# ------------------------------------------------------------------ #
# Auth                                                                 #
# ------------------------------------------------------------------ #

def test_interview_prep_requires_authentication(client: TestClient):
    resp = client.post(_interview_url(str(uuid.uuid4())))
    assert resp.status_code == 401


# ------------------------------------------------------------------ #
# 404 cases                                                            #
# ------------------------------------------------------------------ #

def test_unknown_application_returns_404(client: TestClient, setup):
    token = setup["token"]
    resp = client.post(
        _interview_url(str(uuid.uuid4())),
        headers=_auth_header(token),
    )
    assert resp.status_code == 404


def test_another_users_application_returns_404(client: TestClient, setup):
    """User B cannot request interview prep for User A's application."""
    token_b, _ = _register_and_login(client)
    app_id = setup["app"]["id"]
    resp = client.post(
        _interview_url(app_id),
        headers=_auth_header(token_b),
    )
    assert resp.status_code == 404


def test_missing_resume_returns_404(client: TestClient):
    """User with no resume gets 404."""
    token, _ = _register_and_login(client)
    job = _create_job(client, token)
    app = _create_app(client, token, job["id"])
    resp = client.post(
        _interview_url(app["id"]),
        headers=_auth_header(token),
    )
    assert resp.status_code == 404
    assert "resume" in resp.json()["detail"].lower()


# ------------------------------------------------------------------ #
# Happy path                                                           #
# ------------------------------------------------------------------ #

def test_interview_prep_returns_200(client: TestClient, setup):
    token = setup["token"]
    app_id = setup["app"]["id"]

    with patch(_PATCH_COACH, new=AsyncMock(return_value=_MOCK_PLAN)):
        resp = client.post(_interview_url(app_id), headers=_auth_header(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_role"] == "Backend Engineer"
    assert body["readiness_score"] == 72
    assert body["application_id"] == app_id
    assert body["job_id"] == setup["job"]["id"]


def test_interview_prep_response_shape(client: TestClient, setup):
    token = setup["token"]
    app_id = setup["app"]["id"]

    with patch(_PATCH_COACH, new=AsyncMock(return_value=_MOCK_PLAN)):
        resp = client.post(_interview_url(app_id), headers=_auth_header(token))

    body = resp.json()
    for field in (
        "application_id", "job_id", "target_role", "readiness_score",
        "interview_questions", "strength_areas", "improvement_areas",
        "preparation_plan", "final_advice",
    ):
        assert field in body, f"missing field: {field}"


def test_interview_questions_shape(client: TestClient, setup):
    token = setup["token"]
    app_id = setup["app"]["id"]

    with patch(_PATCH_COACH, new=AsyncMock(return_value=_MOCK_PLAN)):
        resp = client.post(_interview_url(app_id), headers=_auth_header(token))

    questions = resp.json()["interview_questions"]
    assert isinstance(questions, list)
    assert len(questions) == 1
    q = questions[0]
    for field in ("question", "category", "difficulty", "ideal_answer_points"):
        assert field in q


def test_coach_called_with_correct_profile_and_role(client: TestClient, setup):
    """InterviewCoachService receives candidate_profile and target_role."""
    token = setup["token"]
    app_id = setup["app"]["id"]

    mock_coach = AsyncMock(return_value=_MOCK_PLAN)
    with patch(_PATCH_COACH, new=mock_coach):
        resp = client.post(_interview_url(app_id), headers=_auth_header(token))

    assert resp.status_code == 200
    args = mock_coach.call_args
    # positional: (candidate_profile, target_role)
    candidate_profile = args[0][0]
    target_role = args[0][1]
    assert isinstance(candidate_profile, dict)
    assert target_role == "Backend Engineer"


# ------------------------------------------------------------------ #
# AI failure                                                           #
# ------------------------------------------------------------------ #

def test_ai_failure_returns_502(client: TestClient, setup):
    from backend.ai.interview_coach import InterviewCoachError

    token = setup["token"]
    app_id = setup["app"]["id"]

    with patch(
        _PATCH_COACH,
        new=AsyncMock(side_effect=InterviewCoachError("Gemini down")),
    ):
        resp = client.post(_interview_url(app_id), headers=_auth_header(token))

    assert resp.status_code == 502
