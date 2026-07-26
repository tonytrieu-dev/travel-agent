"""require age and fitness_level, drop budget_usd

Revision ID: e2fd12c1788c
Revises: b7c1e94a2f30
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2fd12c1788c"
down_revision: str | None = "b7c1e94a2f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Age/fitness predate being mandatory. execution_event and booking_transition are append-only
# (reject_audit_row_mutation rejects any DELETE against them), so a legacy trip that ever
# produced an execution_event or a hitl_booking_log entry cannot be deleted — its trip_request
# row is kept and backfilled instead. Only a trip with zero audit trail is safe to remove.
_LEGACY_TRIP_WITH_NO_AUDIT_TRAIL = (
    "(age IS NULL OR fitness_level IS NULL) "
    "AND NOT EXISTS (SELECT 1 FROM execution_event WHERE execution_event.trip_request_id = trip_request.id) "
    "AND NOT EXISTS (SELECT 1 FROM hitl_booking_log WHERE hitl_booking_log.trip_request_id = trip_request.id)"
)


def upgrade() -> None:
    op.execute(
        "DELETE FROM flight_search_result WHERE trip_request_id IN ("
        f"SELECT id FROM trip_request WHERE {_LEGACY_TRIP_WITH_NO_AUDIT_TRAIL})"
    )
    op.execute(
        "DELETE FROM itinerary WHERE trip_request_id IN ("
        f"SELECT id FROM trip_request WHERE {_LEGACY_TRIP_WITH_NO_AUDIT_TRAIL})"
    )
    op.execute(f"DELETE FROM trip_request WHERE {_LEGACY_TRIP_WITH_NO_AUDIT_TRAIL}")

    # Every trip still null here has a permanent audit trail (excluded above) — its history is
    # kept, and age/fitness is backfilled with a neutral placeholder purely to satisfy NOT NULL.
    op.execute("UPDATE trip_request SET age = 30 WHERE age IS NULL")
    op.execute("UPDATE trip_request SET fitness_level = 'MODERATE' WHERE fitness_level IS NULL")

    op.alter_column("trip_request", "age", existing_type=sa.Integer(), nullable=False)
    op.alter_column(
        "trip_request",
        "fitness_level",
        existing_type=sa.Enum("LOW", "MODERATE", "HIGH", name="fitnesslevel"),
        nullable=False,
    )
    op.drop_column("trip_request", "budget_usd")


def downgrade() -> None:
    op.add_column("trip_request", sa.Column("budget_usd", sa.Float(), nullable=True))
    op.alter_column("trip_request", "age", existing_type=sa.Integer(), nullable=True)
    op.alter_column(
        "trip_request",
        "fitness_level",
        existing_type=sa.Enum("LOW", "MODERATE", "HIGH", name="fitnesslevel"),
        nullable=True,
    )
    # Deleted legacy rows and the backfilled placeholder are gone for good; downgrade restores
    # schema shape, not data.
