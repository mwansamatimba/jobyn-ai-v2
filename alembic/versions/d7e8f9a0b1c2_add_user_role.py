"""add durable user role for administrator authorization

Revision ID: d7e8f9a0b1c2
Revises: c1a2b3d4e5f6
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c1a2b3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(length=32),
                nullable=False,
                server_default="user",
            )
        )
        batch_op.create_index("ix_users_role", ["role"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_role")
        batch_op.drop_column("role")
