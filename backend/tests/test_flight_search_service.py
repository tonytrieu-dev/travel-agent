"""Boundary tests for FlightSearchService: the single implementation behind both the
/flights/search route (full persistence + cross-trip cache) and the planner's search_flights
tool (same-trip cache + live search only, never persists, never reaches across trips)."""

import pytest

from app.repositories.trips_repository import TripError
from app.services.flight_search import FlightSearchService
from tests.conftest import FlightSearchSpy
from tests.db_helpers import run_db


def test_missing_trip_raises_trip_error() -> None:
    async def _work(session):
        service = FlightSearchService(session, FlightSearchSpy())
        with pytest.raises(TripError) as excinfo:
            await service.search(999_999)
        return excinfo.value

    error = run_db(_work)
    assert error.status_code == 404
    assert error.code.value == "trip_not_found"
