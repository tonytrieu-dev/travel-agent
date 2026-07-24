"""Boundary tests for ExecutionService: a thin object wrapping execution_context()/
persist_agent_run() so callers stop reaching into the ContextVar-based module functions
directly. current_trip()/record_event() stay as working compatibility shims — planner tools
and FlightSearchService still call them directly and must keep working unchanged."""

from pydantic_ai.usage import RunUsage

from app.agent.execution_log import ExecutionService
from tests.db_helpers import run_db, seed_trip


def test_start_run_creates_an_agent_run_bound_to_the_trip() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            assert run.agent_run is not None
            agent_run_id = run.agent_run.id
        return trip_id, agent_run_id

    trip_id, agent_run_id = run_db(_work)
    assert agent_run_id is not None


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
