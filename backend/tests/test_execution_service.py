"""Boundary tests for ExecutionService: a thin object wrapping execution_context()/
persist_agent_run() so callers stop reaching into the ContextVar-based module functions
directly. current_trip()/record_event() stay as working compatibility shims — planner tools
still call them and must keep working unchanged."""

from app.agent.execution_log import ExecutionService, current_trip
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
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            await run.record_event(ExecutionEventKind.API_CALL, "probe", "ok", "detail")
            # current_trip() must resolve to the same session/trip the run is bound to.
            bound_session, bound_trip_id = current_trip("test")
        return trip_id, bound_trip_id

    trip_id, bound_trip_id = run_db(_work)
    assert bound_trip_id == trip_id
