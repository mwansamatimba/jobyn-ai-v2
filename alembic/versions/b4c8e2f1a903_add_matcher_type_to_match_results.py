"""add matcher_type to match_results

Revision ID: b4c8e2f1a903
Revises: a119af425295
Create Date: 2026-08-11 14:35:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c8e2f1a903"
down_revision: str | None = "a119af425295"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "match_results",
        sa.Column(
            "matcher_type",
            sa.String(length=20),
            server_default="deterministic",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("match_results", "matcher_type")
