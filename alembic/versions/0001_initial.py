"""initial schema revision

Revision ID: 0001
Revises:
Create Date: 2026-08-01

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema changes yet; models are added in later revisions."""
    pass


def downgrade() -> None:
    """No schema changes yet."""
    pass
