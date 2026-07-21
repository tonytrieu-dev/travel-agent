"""DBOS durable-execution wiring: booking execute and the agent run replay-safe across crashes.

Both entry points take only serializable arguments (a DBOS constraint) and rebuild whatever
session/provider they need internally, rather than receiving them injected — this is the only
change from the plain, already-tested versions of the functions they call.
"""

from dbos import DBOS, DBOSConfig

from app.adapters.activities_tavily import TavilyActivityProvider
from app.adapters.flights_searchapi import get_flight_provider
from app.agent.execution_log import execution_context
from app.agent.observability import persist_agent_run
from app.agent.planner import PlannerDeps, agent, default_usage_limits
from app.config import GROQ_MODEL, get_settings
from app.db import get_session_factory
from app.models import FlightSearchResult
from app.rate_limit import acquire_agent_run_slot, release_agent_run_slot
from app.repositories import booking_repository as repository
from app.schemas import BookingLogOut, ClarificationOut, ItineraryOut


def launch_dbos() -> None:
    settings = get_settings()
    DBOS(config=DBOSConfig(name="travel-agent", system_database_url=settings.dbos_database_url))
    DBOS.launch()


def shutdown_dbos() -> None:
    DBOS.destroy()


@DBOS.step(name="fetch_booking_options")
async def _fetch_booking_options_step(flight: FlightSearchResult) -> list[dict]:
    provider = get_flight_provider(get_settings())
    return await provider.fetch_booking_options(flight.booking_token)


@DBOS.workflow(name="execute_booking")
async def execute_booking_durable(log_id: int) -> BookingLogOut:
    async with get_session_factory()() as session:
        booking = await repository.execute_booking(session, log_id, _fetch_booking_options_step)
        return BookingLogOut.model_validate(booking)


@DBOS.workflow(name="run_planner")
async def _run_planner_workflow(trip_id: int, prompt: str) -> ItineraryOut | ClarificationOut:
    settings = get_settings()
    deps = PlannerDeps(
        flight_provider=get_flight_provider(settings),
        activity_provider=TavilyActivityProvider(settings.tavily_api_key.get_secret_value()),
    )
    async with get_session_factory()() as session, execution_context(session, trip_id):
        result = await agent.run(prompt, deps=deps, usage_limits=default_usage_limits())
        await persist_agent_run(
            session,
            trip_request_id=trip_id,
            model=GROQ_MODEL,
            message_history=result.all_messages(),
            usage=result.usage,
        )
    return result.output


async def run_planner_durable(trip_id: int, prompt: str) -> ItineraryOut | ClarificationOut:
    """Not itself a DBOS workflow: the concurrency slot is plain in-process state, and acquiring
    it inside a replayable workflow body risks a double-acquire if DBOS re-enters that body
    during its own internal record/persist resolution (observed empirically) — so the slot wraps
    the durable call from the outside instead."""
    await acquire_agent_run_slot()
    try:
        return await _run_planner_workflow(trip_id, prompt)
    finally:
        release_agent_run_slot()
