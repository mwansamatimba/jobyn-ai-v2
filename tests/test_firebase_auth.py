"""Focused tests for the experimental Firebase authentication compatibility layer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest

from backend.auth.firebase import FirebaseTokenError
from backend.auth.jwt import InvalidTokenError
from backend.api import deps


def _jwt_with_algorithm(algorithm: str) -> str:
    """Create a structurally valid JWT header without requiring a signing key."""
    return jwt.encode({"sub": "ignored"}, key="test-secret", algorithm=algorithm)


@pytest.mark.asyncio
async def test_valid_firebase_token_maps_by_verified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(id="jobyn-user", is_active=True)
    repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))
    verify = lambda token: {  # noqa: E731
        "uid": "firebase-uid",
        "email": "User@Example.com",
        "email_verified": True,
    }
    monkeypatch.setattr(deps, "verify_firebase_id_token", verify)

    result = await deps.get_current_user(_jwt_with_algorithm("RS256"), repository)

    assert result is user
    repository.get_by_email.assert_awaited_once_with("user@example.com")


@pytest.mark.asyncio
async def test_invalid_firebase_token_never_falls_back_to_jobyn_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace(get_by_email=AsyncMock())
    fallback = lambda token: pytest.fail("Jobyn JWT verifier must not receive an RS256 token")
    monkeypatch.setattr(deps, "verify_firebase_id_token", lambda token: (_ for _ in ()).throw(
        FirebaseTokenError("expired")
    ))
    monkeypatch.setattr(deps, "verify_token", fallback)

    with pytest.raises(InvalidTokenError):
        await deps.get_current_user(_jwt_with_algorithm("RS256"), repository)

    repository.get_by_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_firebase_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = SimpleNamespace(get_by_email=AsyncMock())
    monkeypatch.setattr(
        deps,
        "verify_firebase_id_token",
        lambda token: (_ for _ in ()).throw(FirebaseTokenError("expired")),
    )

    with pytest.raises(InvalidTokenError):
        await deps.get_current_user(_jwt_with_algorithm("RS256"), repository)


@pytest.mark.asyncio
async def test_firebase_user_is_provisioned_without_schema_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_user = SimpleNamespace(is_active=True)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository = SimpleNamespace(
        session=session,
        get_by_email=AsyncMock(return_value=None),
        create=AsyncMock(return_value=created_user),
    )
    monkeypatch.setattr(
        deps,
        "verify_firebase_id_token",
        lambda token: {
            "uid": "firebase-uid",
            "email": "new@example.com",
            "email_verified": True,
            "name": "New User",
        },
    )

    result = await deps.get_current_user(_jwt_with_algorithm("RS256"), repository)

    assert result is created_user
    repository.create.assert_awaited_once()
    kwargs = repository.create.await_args.kwargs
    assert kwargs["email"] == "new@example.com"
    assert kwargs["full_name"] == "New User"
    assert kwargs["is_verified"] is True
    assert kwargs["hashed_password"].startswith("$2b$")
    assert len(kwargs["hashed_password"]) == 60
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_jobyn_hs256_token_still_uses_original_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(is_active=True)
    repository = SimpleNamespace(get=AsyncMock(return_value=user))
    monkeypatch.setattr(
        deps,
        "verify_token",
        lambda token: {"sub": "550e8400-e29b-41d4-a716-446655440000"},
    )
    firebase_verify = AsyncMock()
    monkeypatch.setattr(deps, "verify_firebase_id_token", firebase_verify)

    result = await deps.get_current_user(_jwt_with_algorithm("HS256"), repository)

    assert result is user
    repository.get.assert_awaited_once()
    firebase_verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_algorithm_is_rejected_without_verifier_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace()
    jobyn_verify = lambda token: pytest.fail("unsupported algorithms must not use Jobyn JWT")
    firebase_verify = lambda token: pytest.fail("unsupported algorithms must not use Firebase")
    monkeypatch.setattr(deps, "verify_token", jobyn_verify)
    monkeypatch.setattr(deps, "verify_firebase_id_token", firebase_verify)

    token = _jwt_with_algorithm("none")
    with pytest.raises(InvalidTokenError):
        await deps.get_current_user(token, repository)
