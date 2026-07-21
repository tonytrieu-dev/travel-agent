"""Guards the automatic ExecutionEvent recorder (Phase 4): events sequence correctly within a
run, resume correctly across multiple runs on the same trip, and recording outside a bound
context fails loud instead of silently dropping a panel entry.
"""

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.agent.execution_log import execution_context, record_event
from app.models import ExecutionEvent, ExecutionEventKind
from tests.db_helpers import run_db, seed_trip


async def _events_for(session, trip_id: int) -> list[ExecutionEvent]:
    result = await session.execute(
        select(ExecutionEvent)
        .where(col(ExecutionEvent.trip_request_id) == trip_id)
        .order_by(col(ExecutionEvent.seq))
    )
    return list(result.scalars())


def test_events_recorded_in_one_run_are_sequential_and_ordered() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        async with execution_context(session, trip_id):
            await record_event(ExecutionEventKind.API_CALL, "search_flights", "ok", "found 3 offers")
            await record_event(ExecutionEventKind.API_CALL, "web_search", "ok", "found 5 results")
        return await _events_for(session, trip_id)

    events = run_db(_work)

    assert [event.seq for event in events] == [1, 2], (
        f"two events recorded in one context must get consecutive seq 1, 2; got "
        f"{[event.seq for event in events]}"
    )
    assert [event.name for event in events] == ["search_flights", "web_search"], (
        "events must persist in call order so the panel timeline reads top-to-bottom correctly"
    )


def test_a_second_run_on_the_same_trip_resumes_seq_instead_of_restarting() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        async with execution_context(session, trip_id):
            await record_event(ExecutionEventKind.PROTOCOL, "first_run_event", "ok", "run 1")
        async with execution_context(session, trip_id):
            await record_event(ExecutionEventKind.PROTOCOL, "second_run_event", "ok", "run 2")
        return await _events_for(session, trip_id)

    events = run_db(_work)

    assert [event.seq for event in events] == [1, 2], (
        f"a re-planned trip's second run must continue the seq from the first run (no reused "
        f"or reset sequence numbers), got {[event.seq for event in events]}"
    )


async def test_record_event_outside_a_bound_context_raises() -> None:
    with pytest.raises(RuntimeError, match="no execution_context bound"):
        await record_event(ExecutionEventKind.API_CALL, "orphan_call", "ok", "should never persist")
