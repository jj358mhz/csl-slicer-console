"""add config capture columns to slicers

Revision ID: d2e7b4c19a60
Revises: 9a3f2c8e1d47
Create Date: 2026-10-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e7b4c19a60"
down_revision: str | None = "9a3f2c8e1d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("slicers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("config_captured_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "config_capture_pending",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("config_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("slicers", schema=None) as batch_op:
        batch_op.drop_column("config_error")
        batch_op.drop_column("config_capture_pending")
        batch_op.drop_column("config_captured_at")
        batch_op.drop_column("config_encrypted")
