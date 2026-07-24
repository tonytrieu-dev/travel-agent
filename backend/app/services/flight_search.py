"""FlightSearchService: the one implementation of flight-offer search, caching, filtering,
persistence, and ordering behind both the /flights/search route and the planner's
search_flights tool. The two callers differ only in persist/allow_cross_trip_cache — the
route persists and reaches across trips, the planner tool trusts only this trip's own
recent search and never writes offers (see planner.search_flights for why)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.adapters.flights_searchapi import FlightProvider, NormalizedFlightOffer
from app.agent.execution_log import record_event
from app.config import FLIGHT_CACHE_TTL_MINUTES
from app.models import (
    AgentRun,
    AgentRunStep,
    AgentStepKind,
    ExecutionEventKind,
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


def _cheapest_first(offers: list[FlightSearchResult]) -> list[FlightSearchResult]:
    """Ascending price order is a backend guarantee, held on every path (fresh live search,
    own-trip reuse, cross-trip cache) so all three agree — the frontend lists offers in the
    order the API returns them."""
    return sorted(offers, key=lambda offer: offer.price_usd)


def _complete_flight_results(
    offers: list[FlightSearchResult], return_date: str | None
) -> list[FlightSearchResult]:
    if return_date is None:
        return offers
    return [
        offer
        for offer in offers
        if offer.raw_offer.get("return_flights")
        and offer.raw_offer.get("booking_token") == offer.booking_token
    ]


def offer_summary(offer: FlightSearchResult | NormalizedFlightOffer) -> dict:
    return {
        "carrier": offer.carrier,
        "price_usd": offer.price_usd,
        "currency": offer.currency,
        "depart_at": offer.depart_at,
        "arrive_at": offer.arrive_at,
        "stops": offer.stops,
    }


def flight_provider_name(provider: FlightProvider) -> str:
    return {
        "LiveSearchApiProvider": "SearchApi",
        "RecordedProvider": "Recorded flights",
    }.get(type(provider).__name__, type(provider).__name__)


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

        same_trip_cache = await self._get_same_trip_cache(trip)
        if same_trip_cache:
            await self._log(
                trip, agent_run, "ok",
                f"{len(same_trip_cache)} offers (reused, already searched within TTL)",
                same_trip_cache, run_started_at, run_started_monotonic,
            )
            return TripFlightSearchOutcome(same_trip_cache, None, "same_trip_cache")
        raise NotImplementedError

    async def _get_same_trip_cache(self, trip: TripRequest) -> list[FlightSearchResult]:
        cutoff = utcnow() - timedelta(minutes=FLIGHT_CACHE_TTL_MINUTES)
        results = list(
            await self._session.scalars(
                select(FlightSearchResult).where(
                    col(FlightSearchResult.trip_request_id) == trip.id,
                    col(FlightSearchResult.created_at) >= cutoff,
                )
            )
        )
        return _cheapest_first(_complete_flight_results(results, trip.return_date))

    async def _log(
        self,
        trip: TripRequest,
        agent_run: AgentRun | None,
        status: str,
        detail: str,
        offers: list[FlightSearchResult] | list[NormalizedFlightOffer],
        run_started_at: datetime,
        run_started_monotonic: float,
        duration_ms: int | None = None,
    ) -> None:
        await record_event(
            ExecutionEventKind.API_CALL, "search_flights", status, detail, duration_ms,
            data={"offers": [offer_summary(offer) for offer in offers]},
            provider=agent_run.model if agent_run is not None else flight_provider_name(self._provider),
        )
        if agent_run is None:
            return
        finished_at = utcnow()
        total_ms = round((time.monotonic() - run_started_monotonic) * 1000)
        assert agent_run.id is not None
        agent_run.status = "completed" if status == "ok" else status
        agent_run.started_at = run_started_at
        agent_run.finished_at = finished_at
        agent_run.total_ms = total_ms
        self._session.add(
            AgentRunStep(
                agent_run_id=agent_run.id, seq=1, kind=AgentStepKind.TOOL, name="search_flights",
                status=status, duration_ms=duration_ms if duration_ms is not None else total_ms,
                input_summary=(
                    f"{trip.origin} to {trip.destination_airport}, "
                    f"{trip.depart_date} to {trip.return_date or 'one-way'}"
                ),
                output_summary=detail, tokens=0,
            )
        )
        await self._session.commit()
