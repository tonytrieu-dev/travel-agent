"""allow rebooking cancelled flights

Revision ID: 9d6e3b7a1c42
Revises: 494ca223a01f
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d6e3b7a1c42"
down_revision: str | None = "494ca223a01f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_booking_trip_flight", "hitl_booking_log", type_="unique")
    op.create_index(
        "uq_booking_trip_flight_active",
        "hitl_booking_log",
        ["trip_request_id", "flight_search_result_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('PENDING_USER_CONFIRMATION', 'CONFIRMED')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_booking_trip_flight_active", table_name="hitl_booking_log")
    op.create_unique_constraint(
        "uq_booking_trip_flight",
        "hitl_booking_log",
        ["trip_request_id", "flight_search_result_id"],
    )
