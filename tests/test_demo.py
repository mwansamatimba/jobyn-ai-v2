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
