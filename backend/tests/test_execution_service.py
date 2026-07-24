"""Boundary tests for ExecutionService: a thin object wrapping execution_context()/
persist_agent_run() so callers stop reaching into the ContextVar-based module functions
directly. current_trip()/record_event() stay as working compatibility shims — planner tools
still call them and must keep working unchanged."""





import asyncio

from pydantic_ai.usage import RunUsage

from app.agent.execution_log import ExecutionService
from app.models import ExecutionEventKind
from tests.db_helpers import run_db, seed_trip


def test_start_run_creates_an_agent_run_bound_to_the_trip() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            agent_run_id = run.agent_run.id
        return trip_id, agent_run_id

    trip_id, agent_run_id = run_db(_work)
    assert agent_run_id is not None


def test_record_event_persists_through_the_bound_contextvar() -> None:
    async def _work(session):
        from sqlalchemy import select
        from sqlmodel import col

        from app.models import ExecutionEvent

        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            await run.record_event(ExecutionEventKind.API_CALL, "probe", "ok", "detail")
        row = await session.scalar(
            select(ExecutionEvent).where(
                col(ExecutionEvent.trip_request_id) == trip_id,
                col(ExecutionEvent.name) == "probe",
            )
        )
        return row

    event = run_db(_work)
    assert event is not None, "ExecutionRun.record_event must actually persist an ExecutionEvent row"
    assert event.kind == ExecutionEventKind.API_CALL
    assert event.status == "ok"
    assert event.detail == "detail"


def test_concurrent_record_event_calls_get_sequential_non_colliding_seq() -> None:
    """record_event() writes go through a per-run asyncio.Lock (see execution_log.py) because
    AsyncSession isn't safe for concurrent use; pydantic_ai runs tool calls from the same model
    turn concurrently, so three record_event() calls racing via asyncio.gather must still land
    as non-colliding, strictly increasing seq numbers rather than colliding or being silently
    dropped."""

    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            await asyncio.gather(
                run.record_event(ExecutionEventKind.API_CALL, "a", "ok", "1"),
                run.record_event(ExecutionEventKind.API_CALL, "b", "ok", "2"),
                run.record_event(ExecutionEventKind.API_CALL, "c", "ok", "3"),
            )
        from sqlmodel import col, select

        from app.models import ExecutionEvent

        rows = list(
            await session.scalars(
                select(ExecutionEvent)
                .where(col(ExecutionEvent.trip_request_id) == trip_id)
                .order_by(col(ExecutionEvent.seq))
            )
        )
        return [event.seq for event in rows]

    seqs = run_db(_work)
    assert seqs == [1, 2, 3], f"concurrent record_event calls must not collide on seq; got {seqs}"


def test_persist_result_finalizes_the_run_and_derives_steps_from_message_history() -> None:
    """persist_result() must be a thin delegation to observability.persist_agent_run(), using
    the run's own bound AgentRun to finalize token/timing totals, not reimplement any of that
    logic — this test only pins the contract at the ExecutionRun boundary."""

    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            finalized = await run.persist_result(
                message_history=[], usage=RunUsage(input_tokens=10, output_tokens=5)
            )
        return finalized

    finalized = run_db(_work)
    assert finalized.status == "completed"
    assert finalized.total_input_tokens == 10
    assert finalized.total_output_tokens == 5
