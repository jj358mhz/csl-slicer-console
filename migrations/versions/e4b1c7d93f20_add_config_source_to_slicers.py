"""add config_source to slicers

Revision ID: e4b1c7d93f20
Revises: d2e7b4c19a60
Create Date: 2026-10-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b1c7d93f20"
down_revision: str | None = "d2e7b4c19a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("slicers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("slicers", schema=None) as batch_op:
        batch_op.drop_column("config_source")
