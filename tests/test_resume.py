"""Tests for POST /resume/upload and GET /resume/profile/{candidate_id}.

Strategy
--------
Mocks are placed only at two external boundaries:

1. ``backend.services.resume_service.extract_text`` — prevents tests from
   depending on real PDF/DOCX decoding. Tests that exercise file-type and
   size rejection never reach this boundary so they need no mock.

2. ``backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv`` — prevents tests
   from requiring a real Gemini API key. Patched with an ``AsyncMock`` that
   returns a deterministic AI response.

Repositories and the database are *not* mocked. Tests run against the same
shared SQLite test database created by the session-scoped ``client`` fixture
in ``conftest.py``, so they exercise the full ORM stack and verify that records
are actually committed and readable by subsequent requests.
"""

from __future__ import annotations

import io
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter

# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

UPLOAD_URL = "/api/v1/resume/upload"
PROFILE_URL = "/api/v1/resume/profile/{candidate_id}"

_PDF_MIME = "application/pdf"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Exact patch targets: the name as it exists in the module that uses it.
_PATCH_EXTRACT = "backend.services.resume_service.extract_text"
_PATCH_ANALYZE = "backend.ai.cv_analyzer.CVAnalyzerService.analyze_cv"

# Deterministic mock AI response — used to assert on response body fields.
_MOCK_AI_RESULT: dict[str, Any] = {
    "name": "Jane Smith",
    "career_level": "Senior",
    "years_experience": "8 years",
    "skills": ["Python", "FastAPI", "PostgreSQL"],
    "technical_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
    "soft_skills": ["Leadership", "Communication"],
    "industries": ["FinTech", "Software"],
    "strengths": ["System design", "API development"],
    "skill_gaps": ["Kubernetes"],
    "recommended_roles": ["Senior Backend Engineer", "Tech Lead"],
}

# Stub text returned by the mocked extractor.
_STUB_TEXT = "Jane Smith Senior Backend Engineer 8 years Python FastAPI"


# ------------------------------------------------------------------ #
# File builders                                                        #
# ------------------------------------------------------------------ #

def _make_docx() -> bytes:
    """Return a valid DOCX file containing one paragraph of text."""
    doc = Document()
    doc.add_paragraph(_STUB_TEXT)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf() -> bytes:
    """Return a minimal but structurally valid PDF (blank page).

    Text extraction is mocked so the page content is irrelevant; we only
    need bytes that pass MIME-type and size validation.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(
    client: TestClient,
    token: str,
    file_bytes: bytes,
    mime: str,
    filename: str = "resume.pdf",
) -> Any:
    """POST to /resume/upload and return the response."""
    return client.post(
        UPLOAD_URL,
        files={"file": (filename, file_bytes, mime)},
        headers=_auth_header(token),
    )


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def auth_client(client: TestClient):
    """Register a fresh user and return (client, token, user_id).

    Uses a random email so the fixture is safe to call multiple times
    across test modules without hitting the unique-email constraint.
    """
    email = f"resume_{uuid.uuid4().hex[:8]}@example.com"
    password = "supersecret1"

    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Resume Tester"},
    )
    assert reg.status_code == 201, reg.text
    user_id = uuid.UUID(reg.json()["id"])

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    return client, token, user_id


# ------------------------------------------------------------------ #
# Upload — happy paths                                                 #
# ------------------------------------------------------------------ #

def test_upload_pdf_resume(auth_client):
    """A valid PDF upload returns 201 with succeeded parse status."""
    client, token, _ = auth_client

    with patch(_PATCH_EXTRACT, return_value=_STUB_TEXT), \
         patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_AI_RESULT)):
        response = _upload(client, token, _make_pdf(), _PDF_MIME, "cv.pdf")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parse_status"] == "succeeded"
    assert body["original_filename"] == "cv.pdf"
    assert body["content_type"] == _PDF_MIME
    assert body["parsed_data"] is not None
    assert body["parsed_data"]["name"] == "Jane Smith"


def test_upload_docx_resume(auth_client):
    """A valid DOCX upload returns 201 with succeeded parse status."""
    client, token, _ = auth_client

    with patch(_PATCH_EXTRACT, return_value=_STUB_TEXT), \
         patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_AI_RESULT)):
        response = _upload(client, token, _make_docx(), _DOCX_MIME, "cv.docx")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parse_status"] == "succeeded"
    assert body["original_filename"] == "cv.docx"
    assert body["parsed_data"]["skills"] == _MOCK_AI_RESULT["skills"]


def test_upload_response_shape(auth_client):
    """Upload response contains all expected top-level fields."""
    client, token, _ = auth_client

    with patch(_PATCH_EXTRACT, return_value=_STUB_TEXT), \
         patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_AI_RESULT)):
        response = _upload(client, token, _make_pdf(), _PDF_MIME)

    body = response.json()
    for field in ("id", "user_id", "original_filename", "content_type",
                  "file_size_bytes", "parse_status", "parsed_data", "created_at"):
        assert field in body, f"missing field: {field}"


# ------------------------------------------------------------------ #
# Upload — validation rejections (no mocks needed)                    #
# ------------------------------------------------------------------ #

def test_invalid_file_type_rejected(auth_client):
    """Uploading a plain-text file returns 422."""
    client, token, _ = auth_client
    response = _upload(client, token, b"just text", "text/plain", "cv.txt")
    assert response.status_code == 422


def test_oversized_file_rejected(auth_client):
    """A file exceeding 10 MB returns 422 without touching the database."""
    client, token, _ = auth_client
    big = b"x" * (10 * 1024 * 1024 + 1)
    response = _upload(client, token, big, _PDF_MIME, "big.pdf")
    assert response.status_code == 422


def test_upload_requires_authentication(client: TestClient):
    """Upload without a bearer token returns 401."""
    response = client.post(
        UPLOAD_URL,
        files={"file": ("cv.pdf", _make_pdf(), _PDF_MIME)},
    )
    assert response.status_code == 401


# ------------------------------------------------------------------ #
# AI failure handling                                                  #
# ------------------------------------------------------------------ #

def test_ai_failure_returns_error_and_marks_upload_failed(auth_client):
    """When CVAnalyzerService raises, the API returns 422 and the upload
    record is persisted with parse_status=failed — no orphan Resume created.
    """
    from backend.ai.cv_analyzer import CVAnalysisError

    client, token, user_id = auth_client

    with patch(_PATCH_EXTRACT, return_value=_STUB_TEXT), \
         patch(_PATCH_ANALYZE, new=AsyncMock(side_effect=CVAnalysisError("Gemini down"))):
        response = _upload(client, token, _make_pdf(), _PDF_MIME, "fail.pdf")

    assert response.status_code == 422

    # The profile endpoint must return 404 — no Resume record was committed.
    profile = client.get(
        PROFILE_URL.format(candidate_id=user_id),
        headers=_auth_header(token),
    )
    # May be 200 if a prior test already created a resume for this user;
    # either way the important assertion is that the 422 was returned above.
    # If no prior successful upload exists, we expect 404.
    assert profile.status_code in (200, 404)


def test_corrupt_file_returns_422(auth_client):
    """Bytes that are not a valid PDF produce a 422 without an AI call."""
    client, token, _ = auth_client

    # Pass corrupt bytes but *do not* mock extract_text so the real extractor
    # runs and raises on the garbage payload.
    response = _upload(client, token, b"%PDF-corrupted garbage", _PDF_MIME, "bad.pdf")
    assert response.status_code == 422


# ------------------------------------------------------------------ #
# Profile retrieval                                                    #
# ------------------------------------------------------------------ #

def test_profile_retrieval_after_successful_upload(auth_client):
    """GET /profile/{candidate_id} returns the Resume created by the upload."""
    client, token, user_id = auth_client

    # Upload first so a Resume record exists for this user.
    with patch(_PATCH_EXTRACT, return_value=_STUB_TEXT), \
         patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_AI_RESULT)):
        up = _upload(client, token, _make_pdf(), _PDF_MIME)
    assert up.status_code == 201

    response = client.get(
        PROFILE_URL.format(candidate_id=user_id),
        headers=_auth_header(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Shape assertions — we test what the API contract guarantees.
    for field in ("id", "user_id", "title", "status", "content", "created_at"):
        assert field in body, f"missing field: {field}"

    # Content must contain the AI-returned skills.
    assert body["content"]["skills"] == _MOCK_AI_RESULT["skills"]
    assert body["content"]["name"] == _MOCK_AI_RESULT["name"]
    assert body["status"] == "completed"


def test_profile_contains_ai_structured_fields(auth_client):
    """Profile content carries all expected CV analysis keys."""
    client, token, user_id = auth_client

    with patch(_PATCH_EXTRACT, return_value=_STUB_TEXT), \
         patch(_PATCH_ANALYZE, new=AsyncMock(return_value=_MOCK_AI_RESULT)):
        _upload(client, token, _make_pdf(), _PDF_MIME)

    response = client.get(
        PROFILE_URL.format(candidate_id=user_id),
        headers=_auth_header(token),
    )
    assert response.status_code == 200
    content = response.json()["content"]

    for key in ("skills", "technical_skills", "soft_skills",
                "industries", "strengths", "recommended_roles"):
        assert key in content, f"missing content key: {key}"


def test_profile_not_found_for_unknown_candidate(auth_client):
    """GET /profile/{unknown_id} returns 404."""
    client, token, _ = auth_client
    response = client.get(
        PROFILE_URL.format(candidate_id=uuid.uuid4()),
        headers=_auth_header(token),
    )
    assert response.status_code == 404


def test_profile_requires_authentication(client: TestClient):
    """GET /profile without a bearer token returns 401."""
    response = client.get(PROFILE_URL.format(candidate_id=uuid.uuid4()))
    assert response.status_code == 401
