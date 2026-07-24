"""add connector_setting table

Revision ID: a3f6d2c81e97
Revises: c2f4a8e9d103
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f6d2c81e97"
down_revision: str | None = "c2f4a8e9d103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slack_enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("connector_setting")
