"""Authenticated user profile endpoints.

Thin REST routes that only wire request payloads to the injected dependencies.
All persistence flows through :class:`UserRepository`; no business logic,
database queries, password or JWT handling exists in this module.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.deps import get_current_user, get_user_repository
from backend.models.user import User
from backend.repositories.user import UserRepository
from backend.schemas.user import UserRead

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


class UserUpdate(BaseModel):
    """Editable profile fields accepted by ``PATCH /users/me``.

    Only safe, client-editable attributes are exposed. Identity, timestamps,
    credentials and role fields are intentionally absent and can never be set
    through this payload.
    """

    full_name: str | None = Field(default=None, max_length=255)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user's profile",
)
async def read_current_user(
    current_user: User = Depends(get_current_user),
) -> UserRead:
    """Return the profile of the authenticated user.

    No database access is performed; the user is already resolved by the
    ``get_current_user`` dependency.

    Args:
        current_user: The authenticated user.

    Returns:
        The authenticated user's public representation.
    """
    return UserRead.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the authenticated user's profile",
)
async def update_current_user(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    repository: UserRepository = Depends(get_user_repository),
) -> UserRead:
    """Update the editable profile fields of the authenticated user.

    Only fields explicitly present in the request body are applied. When the
    body is empty, the current profile is returned unchanged.

    Args:
        data: The validated update payload.
        current_user: The authenticated user.
        repository: The injected :class:`UserRepository`.

    Returns:
        The updated user's public representation.
    """
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        return UserRead.model_validate(current_user)

    updated = await repository.update(current_user, **updates)
    return UserRead.model_validate(updated)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Return a public user profile by id",
)
async def read_user(
    user_id: uuid.UUID,
    repository: UserRepository = Depends(get_user_repository),
) -> UserRead:
    """Return a user's public profile by UUID.

    Args:
        user_id: The public UUID of the target user.
        repository: The injected :class:`UserRepository`.

    Returns:
        The requested user's public representation.

    Raises:
        HTTPException: With status ``404`` when the user does not exist.
    """
    user = await repository.get(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return UserRead.model_validate(user)
