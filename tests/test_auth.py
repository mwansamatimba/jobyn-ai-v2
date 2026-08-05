"""End-to-end tests for the authentication module through the HTTP API."""

from fastapi.testclient import TestClient

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me_flow(client: TestClient) -> None:
    register = client.post(
        REGISTER_URL,
        json={
            "email": "User@Example.com",
            "password": "supersecret1",
            "full_name": "Ada Lovelace",
        },
    )
    assert register.status_code == 201
    body = register.json()
    assert body["email"] == "user@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["is_active"] is True
    assert "hashed_password" not in body
    assert "id" in body

    login = client.post(
        LOGIN_URL,
        json={"email": "user@example.com", "password": "supersecret1"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"
    assert login.json()["expires_in"] > 0

    me = client.get(ME_URL, headers=_auth_header(token))
    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    payload = {"email": "dup@example.com", "password": "supersecret1"}
    assert client.post(REGISTER_URL, json=payload).status_code == 201
    assert client.post(REGISTER_URL, json=payload).status_code == 409


def test_register_rejects_short_password(client: TestClient) -> None:
    response = client.post(
        REGISTER_URL,
        json={"email": "short@example.com", "password": "tiny"},
    )
    assert response.status_code == 422


def test_login_rejects_wrong_password(client: TestClient) -> None:
    client.post(
        REGISTER_URL,
        json={"email": "wrongpw@example.com", "password": "supersecret1"},
    )
    response = client.post(
        LOGIN_URL,
        json={"email": "wrongpw@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


def test_login_rejects_unknown_email(client: TestClient) -> None:
    response = client.post(
        LOGIN_URL,
        json={"email": "ghost@example.com", "password": "supersecret1"},
    )
    assert response.status_code == 401


def test_me_requires_token(client: TestClient) -> None:
    assert client.get(ME_URL).status_code == 401
    bad = client.get(ME_URL, headers=_auth_header("not.a.real.token"))
    assert bad.status_code == 401
