"""Trip lifecycle: create/update a trip, search flights against it, and generate its itinerary.

Mirrors booking_repository's shape (a domain error carrying code/status/detail, thin functions
taking an already-open session) so the two repositories read the same way.
"""

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
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
    ExecutionEvent,
    ExecutionEventKind,
    FlightResultSource,
    FlightSearchResult,
    Itinerary,
    TripRequest,
    TripStatus,
    utcnow,
)
from app.schemas import (
    ClarificationOut,
    ErrorCode,
    ItineraryOut,
    TripRequestCreate,
    TripRequestUpdate,
    validate_trip_dates,
)

PlannerRunner = Callable[[int, str], Awaitable[ItineraryOut | ClarificationOut]]


class TripError(Exception):
    """A domain rejection carrying the client-facing code, HTTP status, and root-cause detail."""

    def __init__(self, code: ErrorCode, status_code: int, detail: str) -> None:
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def create_trip(session: AsyncSession, user_id: int, data: TripRequestCreate) -> TripRequest:
    trip = TripRequest(user_id=user_id, **data.model_dump())
    session.add(trip)
    await session.commit()
    return trip


async def get_trip(session: AsyncSession, trip_id: int) -> TripRequest:
    """Fetch a persisted trip so the frontend can recover it after a page refresh."""
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")
    return trip


# Changing any of these invalidates a trip's flight search and itinerary: the stored offers were
# priced for the old route/dates, and the itinerary was researched for the old destination.
_CRITERIA_FIELDS = ("origin", "destination", "destination_airport", "depart_date", "return_date")


async def _invalidate_trip_derived_data(session: AsyncSession, trip_id: int) -> None:
    """Drop cached flight offers and any generated itinerary so a later /flights/search or /plan
    can't hand back data computed for the trip's previous criteria."""
    stale_flight_results = await session.scalars(
        select(FlightSearchResult).where(col(FlightSearchResult.trip_request_id) == trip_id)
    )
    for flight_result in stale_flight_results:
        await session.delete(flight_result)
    stale_itinerary = await session.scalar(
        select(Itinerary).where(col(Itinerary.trip_request_id) == trip_id)
    )
    if stale_itinerary is not None:
        await session.delete(stale_itinerary)


async def update_trip(session: AsyncSession, trip_id: int, data: TripRequestUpdate) -> TripRequest:
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    criteria_before = {field_name: getattr(trip, field_name) for field_name in _CRITERIA_FIELDS}
    for field_name, value in data.model_dump(exclude_unset=True).items():
        setattr(trip, field_name, value)

    try:
        validate_trip_dates(trip.depart_date, trip.return_date)
    except ValueError as error:
        await session.rollback()
        raise TripError(ErrorCode.VALIDATION_ERROR, 422, str(error)) from error

    criteria_changed = any(
        getattr(trip, field_name) != criteria_before[field_name] for field_name in _CRITERIA_FIELDS
    )
    if criteria_changed:
        await _invalidate_trip_derived_data(session, trip_id)
        trip.status = TripStatus.CREATED

    await session.commit()
    return trip


def _to_flight_result(
    trip_id: int, *, offer_index: int, source_offer: FlightSearchResult, source: FlightResultSource
) -> FlightSearchResult:
    return FlightSearchResult(
        trip_request_id=trip_id,
        offer_index=offer_index,
        carrier=source_offer.carrier,
        price_usd=source_offer.price_usd,
        currency=source_offer.currency,
        depart_at=source_offer.depart_at,
        arrive_at=source_offer.arrive_at,
        stops=source_offer.stops,
        booking_token=source_offer.booking_token,
        raw_offer=source_offer.raw_offer,
        source=source,
    )


def _cheapest_first(offers: list[FlightSearchResult]) -> list[FlightSearchResult]:
    """The take-home requires surfacing the cheapest flights, and the frontend lists offers in
    the order the API returns them — so ascending price order is a backend guarantee, held on
    every path (fresh live search, own-trip reuse, cross-trip cache) so all three agree."""
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


async def _record_search_flights_run(
    session: AsyncSession,
    agent_run: AgentRun,
    trip: TripRequest,
    started_at: datetime,
    started_monotonic: float,
    status: str,
    detail: str,
    offers: list[FlightSearchResult] | list[NormalizedFlightOffer],
    provider_duration_ms: int | None = None,
) -> None:
    await record_event(
        ExecutionEventKind.API_CALL,
        "search_flights",
        status,
        detail,
        provider_duration_ms,
        data={"offers": [offer_summary(offer) for offer in offers]},
        provider=agent_run.model,
    )
    finished_at = utcnow()
    total_ms = round((time.monotonic() - started_monotonic) * 1000)
    agent_run.status = "completed" if status == "ok" else status
    agent_run.total_ms = total_ms
    agent_run.started_at = started_at
    agent_run.finished_at = finished_at
    assert agent_run.id is not None
    session.add(
        AgentRunStep(
            agent_run_id=agent_run.id,
            seq=1,
            kind=AgentStepKind.TOOL,
            name="search_flights",
            status=status,
            duration_ms=provider_duration_ms if provider_duration_ms is not None else total_ms,
            input_summary=(
                f"{trip.origin} to {trip.destination_airport}, "
                f"{trip.depart_date} to {trip.return_date or 'one-way'}"
            ),
            output_summary=detail,
            tokens=0,
        )
    )
    await session.commit()


async def get_recent_flight_results(session: AsyncSession, trip_id: int) -> list[FlightSearchResult]:
    """Extracted so the planner agent's search_flights tool can reuse this same cache check."""
    cutoff = utcnow() - timedelta(minutes=FLIGHT_CACHE_TTL_MINUTES)
    trip = await session.get(TripRequest, trip_id)
    results = list(
        await session.scalars(
            select(FlightSearchResult).where(
                col(FlightSearchResult.trip_request_id) == trip_id,
                col(FlightSearchResult.created_at) >= cutoff,
            )
        )
    )
    return _cheapest_first(
        _complete_flight_results(results, trip.return_date if trip is not None else None)
    )


async def search_flights(
    session: AsyncSession, trip_id: int, provider: FlightProvider, agent_run: AgentRun
) -> tuple[list[FlightSearchResult], str | None]:
    run_started_at = utcnow()
    run_started_monotonic = time.monotonic()
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    cutoff = utcnow() - timedelta(minutes=FLIGHT_CACHE_TTL_MINUTES)

    # A repeat call on this same trip within the TTL is a safe retry, not a new search: return
    # what's already stored instead of appending another duplicate batch (POST here must be
    # idempotent within the TTL window, or every retry would multiply the trip's stored offers).
    own_recent_results = await get_recent_flight_results(session, trip_id)
    if own_recent_results:
        await _record_search_flights_run(
            session,
            agent_run,
            trip,
            run_started_at,
            run_started_monotonic,
            "ok",
            f"{len(own_recent_results)} offers (reused, already searched within TTL)",
            own_recent_results,
        )
        return own_recent_results, None

    cache_source_trip_id = await session.scalar(
        select(FlightSearchResult.trip_request_id)
        .join(TripRequest, col(FlightSearchResult.trip_request_id) == col(TripRequest.id))
        .where(
            col(TripRequest.origin) == trip.origin,
            col(TripRequest.destination_airport) == trip.destination_airport,
            col(TripRequest.depart_date) == trip.depart_date,
            col(TripRequest.return_date) == trip.return_date,
            col(FlightSearchResult.created_at) >= cutoff,
        )
        .order_by(col(FlightSearchResult.created_at).desc())
        .limit(1)
    )

    cached_source_offers: list[FlightSearchResult] = []
    if cache_source_trip_id is not None:
        cached_source_offers = list(
            await session.scalars(
                select(FlightSearchResult).where(
                    col(FlightSearchResult.trip_request_id) == cache_source_trip_id
                )
            )
        )
    cached_source_offers = _complete_flight_results(cached_source_offers, trip.return_date)
    if cached_source_offers:
        results = [
            _to_flight_result(
                trip_id,
                offer_index=source_offer.offer_index,
                source_offer=source_offer,
                source=FlightResultSource.CACHED,
            )
            for source_offer in cached_source_offers
        ]
        session.add_all(results)
        trip.status = TripStatus.FLIGHTS_SEARCHED
        await session.commit()
        await _record_search_flights_run(
            session,
            agent_run,
            trip,
            run_started_at,
            run_started_monotonic,
            "ok",
            f"{len(results)} offers (cached from an identical route/date search)",
            results,
        )
        return _cheapest_first(results), None

    provider_started_monotonic = time.monotonic()
    outcome = await provider.search_offers(
        trip.origin, trip.destination_airport, trip.depart_date, trip.return_date
    )
    provider_duration_ms = round((time.monotonic() - provider_started_monotonic) * 1000)
    results = [
        FlightSearchResult(
            trip_request_id=trip_id,
            offer_index=index,
            carrier=offer.carrier,
            price_usd=offer.price_usd,
            currency=offer.currency,
            depart_at=offer.depart_at,
            arrive_at=offer.arrive_at,
            stops=offer.stops,
            booking_token=offer.booking_token,
            raw_offer=offer.raw_offer,
            source=FlightResultSource.LIVE,
        )
        for index, offer in enumerate(outcome.offers)
    ]
    session.add_all(results)
    if results:
        trip.status = TripStatus.FLIGHTS_SEARCHED
    await session.commit()
    await _record_search_flights_run(
        session,
        agent_run,
        trip,
        run_started_at,
        run_started_monotonic,
        "ok" if outcome.unavailable_reason is None else "unavailable",
        f"{len(results)} offers" if outcome.unavailable_reason is None else outcome.unavailable_reason,
        outcome.offers,
        provider_duration_ms,
    )
    return _cheapest_first(results), outcome.unavailable_reason


def _build_planner_prompt(trip: TripRequest) -> str:
    parts = [
        f"Plan a trip from {trip.origin} to {trip.destination} (airport {trip.destination_airport}), "
        f"departing {trip.depart_date}"
        + (f", returning {trip.return_date}." if trip.return_date else " (one-way).")
    ]
    if trip.age is not None:
        parts.append(f"Traveler age: {trip.age}.")
    if trip.fitness_level is not None:
        parts.append(f"Fitness level: {trip.fitness_level.value}.")
    if trip.budget_usd is not None:
        parts.append(f"Budget: ${trip.budget_usd:.2f} USD.")
    return " ".join(parts)


async def get_or_create_itinerary(
    session: AsyncSession, trip_id: int, run_planner: PlannerRunner
) -> ItineraryOut | ClarificationOut:
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    existing = await session.scalar(
        select(Itinerary).where(col(Itinerary.trip_request_id) == trip_id)
    )
    if existing is not None:
        return ItineraryOut(days=existing.days)

    output = await run_planner(trip_id, _build_planner_prompt(trip))
    if isinstance(output, ClarificationOut):
        return output

    session.add(
        Itinerary(trip_request_id=trip_id, days=[day.model_dump() for day in output.days])
    )
    trip.status = TripStatus.ITINERARY_READY
    await session.commit()
    return output


async def get_execution_panel(
    session: AsyncSession, trip_id: int
) -> tuple[
    list[tuple[AgentRun, list[AgentRunStep], list[ExecutionEvent]]],
    list[ExecutionEvent],
]:
    """Every run with its own steps/events, plus the same event stream for LiveActivity."""
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    agent_runs = list(
        await session.scalars(
            select(AgentRun)
            .where(col(AgentRun.trip_request_id) == trip_id)
            .order_by(col(AgentRun.started_at).desc())
        )
    )
    all_steps = list(
        await session.scalars(
            select(AgentRunStep)
            .where(col(AgentRunStep.agent_run_id).in_([run.id for run in agent_runs]))
            .order_by(col(AgentRunStep.seq))
        )
    )
    steps_by_run_id: dict[int, list[AgentRunStep]] = defaultdict(list)
    for step in all_steps:
        steps_by_run_id[step.agent_run_id].append(step)
    events = list(
        await session.scalars(
            select(ExecutionEvent)
            .where(col(ExecutionEvent.trip_request_id) == trip_id)
            .order_by(col(ExecutionEvent.seq))
        )
    )
    events_by_run_id: dict[int, list[ExecutionEvent]] = defaultdict(list)
    for event in events:
        if event.agent_run_id is not None:
            events_by_run_id[event.agent_run_id].append(event)
    runs_with_details = [
        (run, steps_by_run_id[run.id], events_by_run_id[run.id])
        for run in agent_runs
        if run.id
    ]
    return runs_with_details, events
