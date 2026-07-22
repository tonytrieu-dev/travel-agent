"""drop deadweight columns

Revision ID: e47ab5e144df
Revises: d8a61532fcf4
Create Date: 2026-07-21 17:07:27.800041
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = 'e47ab5e144df'
down_revision: str | None = 'd8a61532fcf4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('user_account', 'is_anonymized')
    op.drop_column('agent_run', 'dbos_workflow_id')
    op.drop_column('itinerary', 'generated_by')


def downgrade() -> None:
    op.add_column(
        'itinerary',
        sa.Column('generated_by', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    )
    op.add_column(
        'agent_run',
        sa.Column('dbos_workflow_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'user_account',
        sa.Column('is_anonymized', sa.Boolean(), nullable=False),
    )
