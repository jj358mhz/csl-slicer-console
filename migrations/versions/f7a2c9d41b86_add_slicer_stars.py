"""add slicer_stars table

Revision ID: f7a2c9d41b86
Revises: e4b1c7d93f20
Create Date: 2026-10-21 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a2c9d41b86"
down_revision: str | None = "e4b1c7d93f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slicer_stars",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("slicer_id", sa.Integer(), nullable=False),
        sa.Column("starred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["slicer_id"], ["slicers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "slicer_id"),
    )


def downgrade() -> None:
    op.drop_table("slicer_stars")
