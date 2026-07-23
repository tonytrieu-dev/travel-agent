"""Guards the planner agent's search_flights tool against re-spending a live search when this
trip already has flight results — the redundant-search bug that inflated a run's context and
was found alongside a real 413 crash (see planner.py's search_flights for the full reasoning).
"""

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.adapters.flights_searchapi import NormalizedFlightOffer
from app.agent.execution_log import execution_context
from app.agent.planner import PlannerDeps, search_flights
from tests.conftest import FlightSearchSpy
from tests.db_helpers import run_db, seed_flight_search_results, seed_trip


def _context(flight_provider: FlightSearchSpy) -> RunContext[PlannerDeps]:
    deps = PlannerDeps(flight_provider=flight_provider, activity_provider=None)  # type: ignore[arg-type]
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def test_reuses_this_trips_cached_flight_results_instead_of_searching_live_again() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        await seed_flight_search_results(session, trip_id)  # defaults to carrier="AF"
        provider = FlightSearchSpy(
            offers=[
                NormalizedFlightOffer(
                    carrier="LIVE-CARRIER",
                    price_usd=999.0,
                    currency="USD",
                    depart_at="2026-08-01T09:00:00",
                    arrive_at="2026-08-01T21:30:00",
                    stops=0,
                    booking_token="tok-live",
                    raw_offer={"price": 999.0},
                )
            ]
        )
        async with execution_context(session, trip_id):
            result = await search_flights(_context(provider), "JFK", "CDG", "2026-08-01", None)
        return provider, result

    provider, result = run_db(_work)

    assert provider.calls == 0, (
        f"a trip with an already-searched flight result must never spend a second live search; "
        f"got {provider.calls} live call(s)"
    )
    assert result["offers"][0]["carrier"] == "AF", (
        f"the returned offer must be the trip's cached result, not the live provider's stand-in "
        f"data; got {result['offers']}"
    )


def test_live_search_uses_the_stored_trips_route_not_the_model_supplied_arguments() -> None:
    """Trust boundary: the model can pass any well-formed departure_id/arrival_id, but the search
    must run against the trip the user actually created (JFK->CDG here), so a compromised or
    confused model can't redirect the flight search to a different route/date."""

    async def _work(session):
        trip_id = await seed_trip(session)  # JFK -> CDG, depart 2026-08-01, one-way
        provider = FlightSearchSpy()
        async with execution_context(session, trip_id):
            # Model-supplied args deliberately point somewhere else entirely.
            await search_flights(_context(provider), "LAX", "SFO", "2027-01-01", "2027-01-08")
        return provider

    provider = run_db(_work)

    assert provider.last_search_params == {
        "departure_id": "JFK",
        "arrival_id": "CDG",
        "outbound_date": "2026-08-01",
        "return_date": None,
    }, (
        f"search_flights must call the provider with the STORED trip's route/dates, not the "
        f"model's LAX/SFO/2027 arguments; got {provider.last_search_params}"
    )
