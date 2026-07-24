"""FlightSearchService: the one implementation of flight-offer search, caching, filtering,
persistence, and ordering behind both the /flights/search route and the planner's
search_flights tool. The two callers differ only in persist/allow_cross_trip_cache — the
route persists and reaches across trips, the planner tool trusts only this trip's own
recent search and never writes offers (see planner.search_flights for why)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.flights_searchapi import FlightProvider
from app.models import (
    AgentRun,
    FlightSearchResult,
    TripRequest,
    utcnow,
)
from app.repositories.trips_repository import ErrorCode, TripError


@dataclass
class TripFlightSearchOutcome:
    offers: list[FlightSearchResult]
    unavailable_reason: str | None
    source: str  # "same_trip_cache" | "cross_trip_cache" | "live"


class FlightSearchService:
    def __init__(self, session: AsyncSession, provider: FlightProvider) -> None:
        self._session = session
        self._provider = provider

    async def search(
        self,
        trip_id: int,
        *,
        agent_run: AgentRun | None = None,
        persist: bool = True,
        allow_cross_trip_cache: bool = True,
    ) -> TripFlightSearchOutcome:
        trip = await self._session.get(TripRequest, trip_id)
        if trip is None:
            raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")
        # Captured once per call so every branch (cache hit, cross-trip cache, live) can
        # finalize agent_run.started_at/total_ms consistently with the pre-refactor
        # _record_search_flights_run, which timed the whole call, not just the provider hop.
        run_started_at = utcnow()
        run_started_monotonic = time.monotonic()
        raise NotImplementedError
