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
