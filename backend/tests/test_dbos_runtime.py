"""Phase 5's last piece: the agent run executes durably through DBOS, not as a bare coroutine.
A ``TestModel`` swap keeps this fast/deterministic — real-model behavior is already covered by
manual verification; this guards the DBOS wiring itself (a plain call would raise before
``DBOS.launch()``, per the library's own contract)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import col

from app.agent.planner import agent as planner_agent
from app.dbos_runtime import run_planner_durable
from app.models import AgentRun
from app.schemas import ClarificationOut, ItineraryOut
from tests.db_helpers import TEST_DATABASE_URL, seed_trip
from pydantic_ai.models.test import TestModel


async def _seed_trip_id() -> int:
    # Not run_db(): that helper wraps its work in asyncio.run(), which cannot be called from
    # inside a pytest-asyncio test's already-running event loop.
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            trip_id = await seed_trip(session)
            await session.commit()
            return trip_id
    finally:
        await engine.dispose()


async def test_run_planner_durable_executes_the_agent_through_a_dbos_workflow(client) -> None:
    trip_id = await _seed_trip_id()

    # Empty itinerary: this test guards DBOS wiring, not grounding, so give the output validator
    # nothing to ground (a dummy itinerary with activities would trip reject_ungrounded_itinerary).
    with planner_agent.override(model=TestModel(call_tools=[], custom_output_args={"days": []})):
        output = await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    assert isinstance(output, ItineraryOut | ClarificationOut), (
        f"run_planner_durable must return the agent's real structured output, got {type(output)}"
    )


async def test_run_planner_durable_persists_a_real_agent_run_for_the_panel(client) -> None:
    """Guards the Phase 6.4 wiring: a durable planner run must leave a real AgentRun row behind
    (tokens + model from the actual run), not just return output with no observability trail."""
    trip_id = await _seed_trip_id()

    # Empty itinerary: this test guards DBOS wiring, not grounding, so give the output validator
    # nothing to ground (a dummy itinerary with activities would trip reject_ungrounded_itinerary).
    with planner_agent.override(model=TestModel(call_tools=[], custom_output_args={"days": []})):
        await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            agent_run = await session.scalar(
                select(AgentRun).where(col(AgentRun.trip_request_id) == trip_id)
            )
    finally:
        await engine.dispose()

    assert agent_run is not None, (
        "run_planner_durable must persist an AgentRun row so /execution has real data to serve, "
        "not silently skip observability"
    )
    assert agent_run.total_input_tokens > 0, (
        f"AgentRun must carry the run's real token usage, got "
        f"total_input_tokens={agent_run.total_input_tokens}"
    )
