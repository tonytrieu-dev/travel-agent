"""Boundary tests for FlightSearchService: the single implementation behind both the
/flights/search route (full persistence + cross-trip cache) and the planner's search_flights
tool (same-trip cache + live search only, never persists, never reaches across trips)."""

import pytest

from app.adapters.flights_searchapi import NormalizedFlightOffer
from app.agent.execution_log import execution_context
from app.repositories.trips_repository import TripError
from app.services.flight_search import FlightSearchService
from tests.conftest import FlightSearchSpy
from tests.db_helpers import run_db, seed_trip


def test_missing_trip_raises_trip_error() -> None:
    async def _work(session):
        service = FlightSearchService(session, FlightSearchSpy())
        with pytest.raises(TripError) as excinfo:
            await service.search(999_999)
        return excinfo.value

    error = run_db(_work)
    assert error.status_code == 404
    assert error.code.value == "trip_not_found"


def test_same_trip_cache_hit_reuses_results_without_calling_provider() -> None:
    async def _work(session):
        from tests.db_helpers import seed_flight_search_results

        trip_id = await seed_trip(session)
        await seed_flight_search_results(session, trip_id)  # carrier="AF" by default
        provider = FlightSearchSpy(
            offers=[
                NormalizedFlightOffer(
                    carrier="LIVE", price_usd=1.0, currency="USD",
                    depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:00:00",
                    stops=0, booking_token="tok", raw_offer={},
                )
            ]
        )
        service = FlightSearchService(session, provider)
        async with execution_context(session, trip_id):
            outcome = await service.search(trip_id)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert provider.calls == 0, "a same-trip cache hit must never call the provider"
    assert outcome.source == "same_trip_cache"
    assert outcome.offers[0].carrier == "AF"
    assert outcome.unavailable_reason is None


def test_cache_hit_preserves_agent_run_timing_metadata() -> None:
    """The pre-refactor _record_search_flights_run set agent_run.started_at/finished_at/
    total_ms on every branch, not just live search — the execution panel's per-run duration
    depends on this even for a cache-hit run. A regression here would silently zero out
    duration for the common case (repeat searches within the TTL)."""

    async def _work(session):
        from app.models import AgentRun
        from tests.db_helpers import seed_flight_search_results

        trip_id = await seed_trip(session)
        await seed_flight_search_results(session, trip_id)
        agent_run = AgentRun(trip_request_id=trip_id, status="running", model="test-model")
        session.add(agent_run)
        await session.commit()
        service = FlightSearchService(session, FlightSearchSpy())
        async with execution_context(session, trip_id):
            await service.search(trip_id, agent_run=agent_run)
        return agent_run

    agent_run = run_db(_work)
    assert agent_run.status == "completed"
    assert agent_run.started_at is not None
    assert agent_run.finished_at is not None
    assert agent_run.total_ms is not None and agent_run.total_ms >= 0, (
        f"a cache-hit run must still report a real elapsed duration, got {agent_run.total_ms}"
    )


def test_expired_cache_invokes_provider_and_persists_cheapest_first() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        provider = FlightSearchSpy(
            offers=[
                NormalizedFlightOffer(
                    carrier="EXPENSIVE", price_usd=900.0, currency="USD",
                    depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:00:00",
                    stops=0, booking_token="tok-a", raw_offer={},
                ),
                NormalizedFlightOffer(
                    carrier="CHEAP", price_usd=100.0, currency="USD",
                    depart_at="2026-08-01T10:00:00", arrive_at="2026-08-01T22:00:00",
                    stops=1, booking_token="tok-b", raw_offer={},
                ),
            ]
        )
        service = FlightSearchService(session, provider)
        async with execution_context(session, trip_id):
            outcome = await service.search(trip_id)
        from tests.db_helpers import get_flight_search_results

        persisted = await get_flight_search_results(session, trip_id)
        return provider, outcome, persisted

    provider, outcome, persisted = run_db(_work)
    assert provider.calls == 1
    assert outcome.source == "live"
    assert [offer.carrier for offer in outcome.offers] == ["CHEAP", "EXPENSIVE"], (
        "results must be cheapest-first regardless of provider order"
    )
    assert len(persisted) == 2, "live search results must be persisted"


def test_unavailable_provider_result_is_reported_honestly() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        provider = FlightSearchSpy(offers=[], unavailable_reason="No cassette for this route")
        service = FlightSearchService(session, provider)
        async with execution_context(session, trip_id):
            return await service.search(trip_id)

    outcome = run_db(_work)
    assert outcome.unavailable_reason == "No cassette for this route"
    assert outcome.offers == []


def test_provider_is_called_with_the_stored_trips_criteria_not_anything_else() -> None:
    """FlightSearchService.search takes no route/date arguments beyond trip_id — this pins
    that guarantee at the service level (not just the old planner-tool call site): whatever
    criteria the trip was created with is what reaches the provider, always, since there is
    no parameter through which a caller could redirect the search."""

    async def _work(session):
        trip_id = await seed_trip(session)  # db_helpers default: JFK -> CDG, 2026-08-01, one-way
        provider = FlightSearchSpy()
        service = FlightSearchService(session, provider)
        async with execution_context(session, trip_id):
            await service.search(trip_id)
        return provider

    provider = run_db(_work)
    assert provider.last_search_params == {
        "departure_id": "JFK",
        "arrival_id": "CDG",
        "outbound_date": "2026-08-01",
        "return_date": None,
    }, f"provider must be called with the trip's own stored criteria, got {provider.last_search_params}"


def test_cross_trip_cache_reuses_identical_route_without_calling_provider() -> None:
    async def _work(session):
        from tests.db_helpers import seed_flight_search_results

        source_trip_id = await seed_trip(session)
        await seed_flight_search_results(session, source_trip_id)
        other_trip_id = await seed_trip(session)  # same default JFK->CDG route/dates
        provider = FlightSearchSpy()
        service = FlightSearchService(session, provider)
        async with execution_context(session, other_trip_id):
            outcome = await service.search(other_trip_id)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert provider.calls == 0
    assert outcome.source == "cross_trip_cache"
    assert outcome.offers[0].carrier == "AF"


def test_cross_trip_cache_disabled_for_planner_tool_falls_through_to_live() -> None:
    async def _work(session):
        from tests.db_helpers import seed_flight_search_results

        source_trip_id = await seed_trip(session)
        await seed_flight_search_results(session, source_trip_id)
        other_trip_id = await seed_trip(session)
        provider = FlightSearchSpy()
        service = FlightSearchService(session, provider)
        async with execution_context(session, other_trip_id):
            outcome = await service.search(other_trip_id, persist=False, allow_cross_trip_cache=False)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert provider.calls == 1, "planner-tool searches must never reach across trips"
    assert outcome.source == "live"


def test_incomplete_round_trip_offers_are_filtered_out_of_cache_reads() -> None:
    async def _work(session):
        from app.models import FlightSearchResult

        trip_id = await seed_trip(session, return_date="2026-08-08")
        session.add(
            FlightSearchResult(
                trip_request_id=trip_id, offer_index=0, carrier="INCOMPLETE", price_usd=50.0,
                currency="USD", depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:00:00",
                stops=0, booking_token="tok", raw_offer={},  # no return_flights paired
            )
        )
        await session.commit()
        # offers=[] so a fall-through to live search is unambiguous: if the filter failed to
        # drop the incomplete cached offer, outcome.offers would be non-empty here.
        provider = FlightSearchSpy(offers=[])
        service = FlightSearchService(session, provider)
        async with execution_context(session, trip_id):
            outcome = await service.search(trip_id)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert outcome.offers == [], "an unpaired round-trip offer must never be served from cache"
    assert outcome.source == "live", "filtering the incomplete cached offer forces a live search"
    assert provider.calls == 1, (
        "the incomplete cached offer must not short-circuit the search — the provider must "
        "actually be invoked, not just happen to return an empty result by coincidence"
    )
