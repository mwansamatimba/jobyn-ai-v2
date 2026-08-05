"""Pydantic contracts for the authentication flow."""

from pydantic import BaseModel, EmailStr, Field


class UserLogin(BaseModel):
    """Payload for the login endpoint."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """An issued access token and its metadata."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
