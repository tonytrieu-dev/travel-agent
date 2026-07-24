"""DBOS durable-execution wiring: booking execute and the agent run replay-safe across crashes.

Both entry points take only serializable arguments (a DBOS constraint) and rebuild whatever
session/provider they need internally, rather than receiving them injected — this is the only
change from the plain, already-tested versions of the functions they call.
"""

from dbos import DBOS, DBOSConfig
from pydantic_ai import AgentRun, UnexpectedModelBehavior
from pydantic_ai.exceptions import UsageLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.activities_tavily import TavilyActivityProvider
from app.adapters.flights_searchapi import get_flight_provider
from app.agent.execution_log import execution_context, record_event
from app.agent.observability import persist_agent_run
from app.agent.planner import PlannerDeps, agent, default_usage_limits
from app.config import CEREBRAS_MODEL, get_settings
from app.db import get_session_factory
from app.models import AgentRun as ObservedAgentRun
from app.models import ExecutionEventKind, FlightSearchResult, TripRequest
from app.rate_limit import acquire_agent_run_slot, release_agent_run_slot
from app.repositories import booking_repository as repository
from app.schemas import BookingLogOut, ClarificationOut, ItineraryOut


async def _persist_failed_run(
    session: AsyncSession,
    trip_id: int,
    agent_run: AgentRun[PlannerDeps, ItineraryOut | ClarificationOut],
    observed_run: ObservedAgentRun,
) -> None:
    # Keeps whatever tool calls ran before the crash on the execution panel, not just successes.
    await persist_agent_run(
        session,
        trip_request_id=trip_id,
        model=CEREBRAS_MODEL,
        message_history=agent_run.ctx.state.message_history,
        usage=agent_run.ctx.state.usage,
        status="failed",
        agent_run=observed_run,
    )


def launch_dbos() -> None:
    settings = get_settings()
    DBOS(config=DBOSConfig(name="travel-agent", system_database_url=settings.dbos_database_url))
    DBOS.launch()


def shutdown_dbos() -> None:
    DBOS.destroy()


@DBOS.step(name="fetch_booking_options")
async def _fetch_booking_options_step(flight: FlightSearchResult) -> list[dict]:
    provider = get_flight_provider(get_settings())
    flights = flight.raw_offer["flights"]
    async with get_session_factory()() as session:
        trip = await session.get(TripRequest, flight.trip_request_id)
    assert trip is not None, "flight references a trip that no longer exists"
    return await provider.fetch_booking_options(
        flight.booking_token,
        departure_id=flights[0]["departure_airport"]["id"],
        arrival_id=flights[-1]["arrival_airport"]["id"],
        outbound_date=flights[0]["departure_airport"]["date"],
        return_date=trip.return_date,
        booking_token_is_resolved=flight.raw_offer.get("booking_token") == flight.booking_token,
    )


@DBOS.workflow(name="execute_booking")
async def execute_booking_durable(log_id: int) -> BookingLogOut:
    async with get_session_factory()() as session:
        booking = await repository.execute_booking(session, log_id, _fetch_booking_options_step)
        return BookingLogOut.model_validate(booking)


@DBOS.workflow(name="run_planner")
async def _run_planner_workflow(trip_id: int, prompt: str) -> ItineraryOut | ClarificationOut:
    settings = get_settings()
    async with (
        get_session_factory()() as session,
        execution_context(session, trip_id, run_model=CEREBRAS_MODEL) as observed_run,
    ):
        assert observed_run is not None
        trip = await session.get(TripRequest, trip_id)
        deps = PlannerDeps(
            flight_provider=get_flight_provider(settings),
            activity_provider=TavilyActivityProvider(settings.tavily_api_key.get_secret_value()),
            fitness_level=trip.fitness_level if trip is not None else None,
        )
        await record_event(
            ExecutionEventKind.PROTOCOL,
            "Pydantic AI",
            "ok",
            "Tool calling, a ReAct-style loop, and JSON Schema structured output.",
        )
        async with agent.iter(prompt, deps=deps, usage_limits=default_usage_limits()) as agent_run:
            try:
                async for node in agent_run:
                    pass
            except Exception as error:
                await _persist_failed_run(session, trip_id, agent_run, observed_run)
                if isinstance(error, UsageLimitExceeded):
                    # A real, expected outcome on a research-heavy trip (see MAX_CONTEXT_TOKENS
                    # in config.py) — ask the user to narrow scope instead of crashing the request.
                    return ClarificationOut(
                        questions=[
                            "This trip needed more research than fits in one planning pass. "
                            "Could you narrow the destination, trip length, or interests so I "
                            "can complete it in fewer steps?"
                        ]
                    )
                if not isinstance(error, UnexpectedModelBehavior):
                    raise
                # The model exhausted its retries without producing a valid itinerary (e.g. no
                # groundable activities) — ask the user instead of crashing the request.
                return ClarificationOut(
                    questions=[
                        "I couldn't find enough verified activity information to complete this "
                        "itinerary. Could you narrow the destination or share specific interests "
                        "to search for?"
                    ]
                )

            result = agent_run.result
            assert result is not None, "agent_run finished iterating without producing a result"
            await persist_agent_run(
                session,
                trip_request_id=trip_id,
                model=CEREBRAS_MODEL,
                message_history=result.all_messages(),
                usage=result.usage,
                agent_run=observed_run,
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
