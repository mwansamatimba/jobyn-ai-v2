"""Administrator account management service.

This service extends the existing User model and authentication stack. It never
returns authentication secrets and records management actions in the existing
ComplianceEvent audit trail.
"""

from __future__ import annotations

import json
from backend.auth.password import hash_password
from backend.models.enums import ComplianceEventType
from backend.models.ingestion import ComplianceEvent
from backend.models.user import User
from backend.schemas.admin_users import (
    AdminAccountCreate,
    AdminAccountList,
    AdminAccountRead,
    AdminAccountUpdate,
    AdminPasswordChange,
)

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_ROLE = "admin"
USER_ROLE = "user"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _audit(
    session: AsyncSession,
    *,
    action: str,
    actor: User,
    target: User,
    details: dict[str, object] | None = None,
) -> None:
    payload = {
        "kind": "admin_account_management",
        "action": action,
        "actor_user_id": str(actor.id),
        "target_user_id": str(target.id),
        **(details or {}),
    }
    session.add(
        ComplianceEvent(
            event_type=ComplianceEventType.MANUAL_REVIEW,
            requester=actor.email,
            notes=json.dumps(payload, sort_keys=True),
        )
    )


class AdminAccountService:
    """Use-case layer for administrator account lifecycle operations."""

    async def list_admins(
        self,
        session: AsyncSession,
        *,
        offset: int,
        limit: int,
    ) -> AdminAccountList:
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(User).where(
                        User.role == ADMIN_ROLE,
                        User.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )
        result = await session.execute(
            select(User)
            .where(User.role == ADMIN_ROLE, User.deleted_at.is_(None))
            .order_by(User.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        items = [AdminAccountRead.model_validate(user) for user in result.scalars().all()]
        return AdminAccountList(items=items, total=total, offset=offset, limit=limit)

    async def get_admin(
        self,
        session: AsyncSession,
        user_id,
    ) -> User:
        user = await session.get(User, user_id)
        if user is None or user.deleted_at is not None or user.role != ADMIN_ROLE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Administrator not found.",
            )
        return user

    async def create_admin(
        self,
        session: AsyncSession,
        *,
        actor: User,
        data: AdminAccountCreate,
    ) -> User:
        email = _normalize_email(str(data.email))
        existing = await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None)).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email address already exists.",
            )

        user = User(
            email=email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
            is_active=True,
            is_verified=True,
            role=ADMIN_ROLE,
        )
        session.add(user)
        try:
            await session.flush()
            _audit(session, action="ADMIN_CREATED", actor=actor, target=user)
            await session.commit()
            await session.refresh(user)
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email address already exists.",
            ) from exc
        return user

    async def update_admin(
        self,
        session: AsyncSession,
        *,
        actor: User,
        target: User,
        data: AdminAccountUpdate,
    ) -> User:
        if target.role != ADMIN_ROLE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Administrator not found.",
            )

        changes: dict[str, object] = {}
        if data.full_name is not None:
            target.full_name = data.full_name
            changes["full_name"] = data.full_name

        resulting_role = data.role if data.role is not None else target.role
        resulting_active = data.is_active if data.is_active is not None else target.is_active

        if resulting_role != ADMIN_ROLE or not resulting_active:
            active_admins = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(User)
                        .where(
                            User.role == ADMIN_ROLE,
                            User.is_active.is_(True),
                            User.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
            )
            if target.is_active and active_admins <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "The last active administrator cannot be deactivated "
                        "or lose administrator privileges."
                    ),
                )

        if data.is_active is not None:
            target.is_active = data.is_active
            changes["is_active"] = data.is_active
        if data.role is not None:
            target.role = data.role
            changes["role"] = data.role

        if not changes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one administrator field must be supplied.",
            )

        if data.role == ADMIN_ROLE and target.is_active:
            action = "ADMIN_ACTIVATED" if changes.get("is_active") is True else "ADMIN_UPDATED"
        elif data.is_active is False or data.role == USER_ROLE:
            action = "ADMIN_DEACTIVATED"
        else:
            action = "ADMIN_UPDATED"

        _audit(session, action=action, actor=actor, target=target, details={"changes": changes})
        await session.commit()
        await session.refresh(target)
        return target

    async def change_password(
        self,
        session: AsyncSession,
        *,
        actor: User,
        target: User,
        data: AdminPasswordChange,
    ) -> User:
        if target.role != ADMIN_ROLE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Administrator not found.",
            )
        target.hashed_password = hash_password(data.password)
        _audit(session, action="ADMIN_PASSWORD_CHANGED", actor=actor, target=target)
        await session.commit()
        await session.refresh(target)
        return target

    async def bootstrap_admin(
        self,
        session: AsyncSession,
        *,
        user: User,
    ) -> User:
        user.role = ADMIN_ROLE
        _audit(
            session,
            action="ADMIN_CREATED",
            actor=user,
            target=user,
            details={"bootstrap": True},
        )
        await session.commit()
        await session.refresh(user)
        return user


__all__ = ["ADMIN_ROLE", "USER_ROLE", "AdminAccountService"]
