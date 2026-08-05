import pytest
from backend.core.errors import AuthenticationError
from backend.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    hashed = get_password_hash("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong-password", hashed)


def test_access_token_round_trip() -> None:
    token = create_access_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_access_token_expiry() -> None:
    token = create_access_token("user-123", expires_minutes=-1)
    with pytest.raises(AuthenticationError):
        decode_token(token)


def test_access_token_includes_extra_claims() -> None:
    token = create_access_token("user-123", extra_claims={"scope": "admin"})
    assert decode_token(token)["scope"] == "admin"
