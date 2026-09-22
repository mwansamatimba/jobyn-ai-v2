"""Pydantic contracts for administrator account management."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class AdminAccountRead(BaseModel):
    """Safe administrator representation; never includes authentication secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: Literal["admin", "user"]
    is_active: bool
    is_verified: bool
    created_at: datetime


class AdminAccountList(BaseModel):
    """Paginated administrator listing."""

    items: list[AdminAccountRead]
    total: int
    offset: int
    limit: int


class AdminAccountCreate(BaseModel):
    """Payload for creating an administrator."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class AdminAccountUpdate(BaseModel):
    """Allowed administrator profile/authorization changes."""

    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    role: Literal["admin", "user"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "AdminAccountUpdate":
        if self.full_name is None and self.is_active is None and self.role is None:
            raise ValueError("At least one administrator field must be supplied.")
        return self


class AdminPasswordChange(BaseModel):
    """Administrator password replacement payload."""

    password: str = Field(min_length=8, max_length=128)
