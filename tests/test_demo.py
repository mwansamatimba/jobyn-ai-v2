"""Minimal tests for the GET /demo endpoint."""

from fastapi.testclient import TestClient


def test_demo_returns_200(client: TestClient):
    """GET /demo serves the HTML demo page."""
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_demo_contains_jobyn_branding(client: TestClient):
    """The demo page includes Jobyn AI branding."""
    resp = client.get("/demo")
    assert b"Jobyn" in resp.content
    assert b"<html" in resp.content.lower()


def test_demo_contains_jobs_api_call(client: TestClient):
    """The demo page references the Jobs API endpoint."""
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert b"/api/v1/jobs" in resp.content


def test_demo_contains_search_api_integration(client: TestClient):
    """The demo page uses the server-side search endpoint."""
    resp = client.get("/demo")
    assert b"/api/v1/jobs/search" in resp.content


def test_demo_contains_date_posted_integration(client: TestClient):
    """The demo page displays the date_posted field from the API."""
    resp = client.get("/demo")
    assert b"date_posted" in resp.content


def test_demo_contains_deterministic_match_call(client: TestClient):
    """The demo page calls the deterministic-match endpoint."""
    resp = client.get("/demo")
    assert b"deterministic-match" in resp.content


def test_demo_external_url_uses_noopener_noreferrer(client: TestClient):
    """External application URLs use safe rel attribute."""
    resp = client.get("/demo")
    assert b"noopener noreferrer" in resp.content


def test_demo_contains_filter_metadata(client: TestClient):
    """The demo page references remote_eligibility and category for enriched display."""
    resp = client.get("/demo")
    assert b"remote_eligibility" in resp.content
    assert b"category" in resp.content
