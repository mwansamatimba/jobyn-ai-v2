"""End-to-end tests for administrator account management."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.core.config import get_settings
from backend.database.base import Base
from backend.database.session import async_session_maker, engine
from backend.models.ingestion import ComplianceEvent

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ADMINS = "/api/v1/admin/users"


@pytest.fixture(autouse=True)
def reset_database() -> None:
    """Isolate each admin-account test from the session-scoped HTTP client DB."""
    async def reset() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(reset())


def _register(client: TestClient, email: str, password: str = "supersecret1") -> None:
    response = client.post(
        REGISTER,
        json={"email": email, "password": password, "full_name": email.split("@")[0]},
    )
    assert response.status_code == 201, response.text


def _login(client: TestClient, email: str, password: str = "supersecret1") -> str:
    response = client.post(LOGIN, json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client: TestClient, email: str) -> str:
    settings = get_settings()
    original = list(settings.ADMIN_EMAILS)
    settings.ADMIN_EMAILS = [email]
    try:
        _register(client, email)
        token = _login(client, email)
        response = client.get(ADMINS, headers=_auth(token))
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["role"] == "admin"
        return token
    finally:
        settings.ADMIN_EMAILS = original


def test_admin_requires_authentication(client: TestClient) -> None:
    response = client.get(ADMINS)
    assert response.status_code == 401


def test_normal_user_cannot_access_admin_accounts(client: TestClient) -> None:
    email = "task7-normal-user@example.com"
    _register(client, email)
    token = _login(client, email)
    response = client.get(ADMINS, headers=_auth(token))
    assert response.status_code == 403


def test_admin_can_access_admin_accounts(client: TestClient) -> None:
    token = _bootstrap(client, "task7-list-admin@example.com")
    response = client.get(ADMINS, headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["items"][0]["role"] == "admin"
    assert "hashed_password" not in response.text
    assert "password" not in response.text


def test_admin_can_create_admin(client: TestClient) -> None:
    token = _bootstrap(client, "task7-create-owner@example.com")
    response = client.post(
        ADMINS,
        headers=_auth(token),
        json={
            "email": "task7-created-admin@example.com",
            "password": "created-secret1",
            "full_name": "Created Administrator",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "task7-created-admin@example.com"
    assert body["role"] == "admin"
    assert body["is_active"] is True
    assert body["is_verified"] is True
    assert "hashed_password" not in body
    assert "password" not in body


def test_duplicate_admin_email_rejected(client: TestClient) -> None:
    token = _bootstrap(client, "task7-duplicate-owner@example.com")
    payload = {
        "email": "task7-duplicate-admin@example.com",
        "password": "created-secret1",
    }
    assert client.post(ADMINS, headers=_auth(token), json=payload).status_code == 201
    response = client.post(ADMINS, headers=_auth(token), json=payload)
    assert response.status_code == 409


def test_invalid_email_rejected(client: TestClient) -> None:
    token = _bootstrap(client, "task7-invalid-email-owner@example.com")
    response = client.post(
        ADMINS,
        headers=_auth(token),
        json={"email": "not-an-email", "password": "created-secret1"},
    )
    assert response.status_code == 422


def test_weak_password_rejected(client: TestClient) -> None:
    token = _bootstrap(client, "task7-weak-password-owner@example.com")
    response = client.post(
        ADMINS,
        headers=_auth(token),
        json={"email": "task7-weak@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_admin_can_update_admin(client: TestClient) -> None:
    token = _bootstrap(client, "task7-update-owner@example.com")
    created = client.post(
        ADMINS,
        headers=_auth(token),
        json={
            "email": "task7-update-target@example.com",
            "password": "created-secret1",
            "full_name": "Before Update",
        },
    )
    target_id = created.json()["id"]
    response = client.patch(
        f"{ADMINS}/{target_id}",
        headers=_auth(token),
        json={"full_name": "After Update"},
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "After Update"


def test_admin_can_deactivate_and_reactivate_admin(client: TestClient) -> None:
    token = _bootstrap(client, "task7-toggle-owner@example.com")
    created = client.post(
        ADMINS,
        headers=_auth(token),
        json={
            "email": "task7-toggle-target@example.com",
            "password": "created-secret1",
        },
    )
    target_id = created.json()["id"]

    disabled = client.patch(
        f"{ADMINS}/{target_id}",
        headers=_auth(token),
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    reactivated = client.patch(
        f"{ADMINS}/{target_id}",
        headers=_auth(token),
        json={"is_active": True},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_admin_can_change_password(client: TestClient) -> None:
    token = _bootstrap(client, "task7-password-owner@example.com")
    created = client.post(
        ADMINS,
        headers=_auth(token),
        json={
            "email": "task7-password-target@example.com",
            "password": "created-secret1",
        },
    )
    target_id = created.json()["id"]

    changed = client.post(
        f"{ADMINS}/{target_id}/password",
        headers=_auth(token),
        json={"password": "changed-secret1"},
    )
    assert changed.status_code == 200
    assert "hashed_password" not in changed.text

    new_token = _login(client, "task7-password-target@example.com", "changed-secret1")
    assert client.get(ADMINS, headers=_auth(new_token)).status_code == 200


def test_last_active_admin_cannot_be_deactivated(client: TestClient) -> None:
    token = _bootstrap(client, "task7-last-admin@example.com")
    me = client.get("/api/v1/auth/me", headers=_auth(token))
    target_id = me.json()["id"]

    response = client.patch(
        f"{ADMINS}/{target_id}",
        headers=_auth(token),
        json={"is_active": False},
    )
    assert response.status_code == 409

    role_response = client.patch(
        f"{ADMINS}/{target_id}",
        headers=_auth(token),
        json={"role": "user"},
    )
    assert role_response.status_code == 409


def test_inactive_admin_cannot_login(client: TestClient) -> None:
    token = _bootstrap(client, "task7-inactive-admin@example.com")
    # Keep the bootstrap admin active while making a second admin inactive.
    created = client.post(
        ADMINS,
        headers=_auth(token),
        json={
            "email": "task7-inactive-target@example.com",
            "password": "created-secret1",
        },
    )
    target_id = created.json()["id"]
    assert client.patch(
        f"{ADMINS}/{target_id}",
        headers=_auth(token),
        json={"is_active": False},
    ).status_code == 200

    login = client.post(
        LOGIN,
        json={
            "email": "task7-inactive-target@example.com",
            "password": "created-secret1",
        },
    )
    assert login.status_code == 401


def test_admin_detail_is_not_available_for_normal_user(client: TestClient) -> None:
    admin_token = _bootstrap(client, "task7-detail-owner@example.com")
    created = client.post(
        ADMINS,
        headers=_auth(admin_token),
        json={"email": "task7-detail-target@example.com", "password": "created-secret1"},
    )
    target_id = created.json()["id"]
    _register(client, "task7-detail-normal@example.com")
    normal_token = _login(client, "task7-detail-normal@example.com")

    response = client.get(f"{ADMINS}/{target_id}", headers=_auth(normal_token))
    assert response.status_code == 403

def test_admin_actions_are_audited(client: TestClient) -> None:
    token = _bootstrap(client, "task7-audit-owner@example.com")
    response = client.post(
        ADMINS,
        headers=_auth(token),
        json={
            "email": "task7-audit-target@example.com",
            "password": "created-secret1",
            "full_name": "Audited Administrator",
        },
    )
    assert response.status_code == 201

    async def read_events() -> list[str]:
        async with async_session_maker() as session:
            result = await session.execute(select(ComplianceEvent.notes))
            return [value for value in result.scalars().all() if value]

    events = asyncio.run(read_events())
    assert any('"action": "ADMIN_CREATED"' in event for event in events)


def test_admin_accounts_appear_in_openapi(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/admin/users" in paths
    assert "/api/v1/admin/users/{user_id}" in paths
    assert "/api/v1/admin/users/{user_id}/password" in paths
    assert "Admin Accounts" in paths["/api/v1/admin/users"]["get"]["tags"]
