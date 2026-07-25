"""add OUTPUT agent step kind and reclassify final_result_* steps

Revision ID: b7c1e94a2f30
Revises: a3f6d2c81e97
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c1e94a2f30"
down_revision: str | None = "a3f6d2c81e97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# pydantic-ai delivers a structured result by calling a synthetic tool named "final_result", or
# "final_result_<Type>" when output_type is a union. These rows were recorded as TOOL, which made
# the itinerary look like an agent tool call alongside search_flights/web_search.
_OUTPUT_TOOL_MATCH = r"name = 'final_result' OR name LIKE 'final\_result\_%'"


def upgrade() -> None:
    # Postgres refuses to *use* a new enum label in the transaction that added it, and Alembic runs
    # every revision of one `upgrade` in a single transaction — so the ADD VALUE has to commit on
    # its own before the backfill below can reference it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE agentstepkind ADD VALUE IF NOT EXISTS 'OUTPUT'")

    op.execute(f"UPDATE agent_run_step SET kind = 'OUTPUT' WHERE {_OUTPUT_TOOL_MATCH}")
    # A refused output attempt has no tool return, which the old code recorded as "no_result".
    # "rejected" says why there is no result: validation refused it and the model was retried.
    op.execute(
        "UPDATE agent_run_step SET status = 'rejected' "
        "WHERE kind = 'OUTPUT' AND status = 'no_result'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE agent_run_step SET status = 'no_result' "
        "WHERE kind = 'OUTPUT' AND status = 'rejected'"
    )
    # Postgres cannot drop an enum label, so recreate the type without OUTPUT. Those rows fold back
    # into TOOL, which is exactly how they were classified before this revision.
    op.execute("ALTER TABLE agent_run_step ALTER COLUMN kind TYPE varchar USING kind::varchar")
    op.execute("UPDATE agent_run_step SET kind = 'TOOL' WHERE kind = 'OUTPUT'")
    op.execute("DROP TYPE agentstepkind")
    op.execute("CREATE TYPE agentstepkind AS ENUM ('MODEL', 'TOOL')")
    op.execute(
        "ALTER TABLE agent_run_step ALTER COLUMN kind TYPE agentstepkind USING kind::agentstepkind"
    )
