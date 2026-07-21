"""DBOS durable-execution wiring: booking execute and the agent run replay-safe across crashes.

Both entry points take only serializable arguments (a DBOS constraint) and rebuild whatever
session/provider they need internally, rather than receiving them injected — this is the only
change from the plain, already-tested versions of the functions they call.
"""

from dbos import DBOS, DBOSConfig

from app.adapters.activities_tavily import TavilyActivityProvider
from app.adapters.flights_searchapi import get_flight_provider
from app.agent.planner import PlannerDeps, agent, default_usage_limits
from app.config import get_settings
from app.db import get_session_factory
from app.models import FlightSearchResult
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
async def run_planner_durable(prompt: str) -> ItineraryOut | ClarificationOut:
    settings = get_settings()
    deps = PlannerDeps(
        flight_provider=get_flight_provider(settings),
        activity_provider=TavilyActivityProvider(settings.tavily_api_key.get_secret_value()),
    )
    result = await agent.run(prompt, deps=deps, usage_limits=default_usage_limits())
    return result.output
