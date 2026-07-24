"""link execution events to agent runs

Revision ID: c2f4a8e9d103
Revises: 9d6e3b7a1c42
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2f4a8e9d103"
down_revision: str | None = "9d6e3b7a1c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("execution_event", sa.Column("agent_run_id", sa.Integer(), nullable=True))
    op.add_column("execution_event", sa.Column("provider", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_execution_event_agent_run",
        "execution_event",
        "agent_run",
        ["agent_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_execution_event_agent_run_id",
        "execution_event",
        ["agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_event_agent_run_id", table_name="execution_event")
    op.drop_constraint(
        "fk_execution_event_agent_run",
        "execution_event",
        type_="foreignkey",
    )
    op.drop_column("execution_event", "agent_run_id")
    op.drop_column("execution_event", "provider")
