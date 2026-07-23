"""Guards the automatic ExecutionEvent recorder (Phase 4): events sequence correctly within a
run and resume correctly across multiple runs on the same trip.
"""

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


def test_recorded_data_survives_as_the_real_structured_payload_not_just_the_detail_string() -> None:
    """detail is a short human summary ("4 offers"); data must carry the real payload behind it
    so the execution panel can render actual offers/results, not just a count."""

    async def _work(session):
        trip_id = await seed_trip(session)
        async with execution_context(session, trip_id):
            await record_event(
                ExecutionEventKind.API_CALL,
                "search_flights",
                "ok",
                "1 offers",
                data={"offers": [{"carrier": "United", "price_usd": 412.0}]},
            )
        return await _events_for(session, trip_id)

    events = run_db(_work)

    assert events[0].data == {"offers": [{"carrier": "United", "price_usd": 412.0}]}, (
        f"data must round-trip the exact structured payload passed to record_event, got "
        f"{events[0].data}"
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
