"""add is_paused to uplynk_accounts

Revision ID: a1c4e8f27b35
Revises: f7a2c9d41b86
Create Date: 2026-11-06 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c4e8f27b35"
down_revision: str | None = "f7a2c9d41b86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("uplynk_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("uplynk_accounts", schema=None) as batch_op:
        batch_op.drop_column("is_paused")
