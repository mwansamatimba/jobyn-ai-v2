"""Administrator account management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_session, require_admin
from backend.models.user import User
from backend.schemas.admin_users import (
    AdminAccountCreate,
    AdminAccountList,
    AdminAccountRead,
    AdminAccountUpdate,
    AdminPasswordChange,
)
from backend.services.admin_accounts import AdminAccountService

router = APIRouter(prefix="/admin/users", tags=["Admin Accounts"])
_service = AdminAccountService()


@router.get("", response_model=AdminAccountList, summary="List administrator accounts")
async def list_admin_accounts(
    current_admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AdminAccountList:
    return await _service.list_admins(session, offset=offset, limit=limit)


@router.post(
    "",
    response_model=AdminAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an administrator account",
)
async def create_admin_account(
    data: AdminAccountCreate,
    current_admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminAccountRead:
    user = await _service.create_admin(session, actor=current_admin, data=data)
    return AdminAccountRead.model_validate(user)


@router.get(
    "/{user_id}",
    response_model=AdminAccountRead,
    summary="Get an administrator account",
)
async def get_admin_account(
    user_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminAccountRead:
    user = await _service.get_admin(session, user_id)
    return AdminAccountRead.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=AdminAccountRead,
    summary="Update an administrator account",
)
async def update_admin_account(
    user_id: uuid.UUID,
    data: AdminAccountUpdate,
    current_admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminAccountRead:
    target = await _service.get_admin(session, user_id)
    user = await _service.update_admin(
        session,
        actor=current_admin,
        target=target,
        data=data,
    )
    return AdminAccountRead.model_validate(user)


@router.post(
    "/{user_id}/password",
    response_model=AdminAccountRead,
    summary="Change an administrator password",
)
async def change_admin_password(
    user_id: uuid.UUID,
    data: AdminPasswordChange,
    current_admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminAccountRead:
    target = await _service.get_admin(session, user_id)
    user = await _service.change_password(
        session,
        actor=current_admin,
        target=target,
        data=data,
    )
    return AdminAccountRead.model_validate(user)


@router.delete(
    "/{user_id}",
    response_model=AdminAccountRead,
    summary="Deactivate an administrator account",
)
async def deactivate_admin_account(
    user_id: uuid.UUID,
    current_admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminAccountRead:
    target = await _service.get_admin(session, user_id)
    user = await _service.update_admin(
        session,
        actor=current_admin,
        target=target,
        data=AdminAccountUpdate(is_active=False),
    )
    return AdminAccountRead.model_validate(user)
