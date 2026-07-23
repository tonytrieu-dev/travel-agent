"""add execution event data column

Revision ID: 494ca223a01f
Revises: e47ab5e144df
Create Date: 2026-07-22 12:46:04.468947
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '494ca223a01f'
down_revision: str | None = 'e47ab5e144df'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('execution_event', sa.Column('data', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('execution_event', 'data')
