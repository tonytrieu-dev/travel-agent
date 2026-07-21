"""Trip lifecycle: create/update a trip, search flights against it, and generate its itinerary.

Mirrors booking_repository's shape (a domain error carrying code/status/detail, thin functions
taking an already-open session) so the two repositories read the same way.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.adapters.flights_searchapi import FlightProvider
from app.config import FLIGHT_CACHE_TTL_MINUTES
from app.models import (
    AgentRun,
    AgentRunStep,
    ExecutionEvent,
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


async def update_trip(session: AsyncSession, trip_id: int, data: TripRequestUpdate) -> TripRequest:
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    for field_name, value in data.model_dump(exclude_unset=True).items():
        setattr(trip, field_name, value)

    try:
        validate_trip_dates(trip.depart_date, trip.return_date)
    except ValueError as error:
        await session.rollback()
        raise TripError(ErrorCode.VALIDATION_ERROR, 422, str(error)) from error

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


async def search_flights(
    session: AsyncSession, trip_id: int, provider: FlightProvider
) -> tuple[list[FlightSearchResult], str | None]:
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    cutoff = utcnow() - timedelta(minutes=FLIGHT_CACHE_TTL_MINUTES)

    # A repeat call on this same trip within the TTL is a safe retry, not a new search: return
    # what's already stored instead of appending another duplicate batch (POST here must be
    # idempotent within the TTL window, or every retry would multiply the trip's stored offers).
    own_recent_results = list(
        await session.scalars(
            select(FlightSearchResult).where(
                col(FlightSearchResult.trip_request_id) == trip_id,
                col(FlightSearchResult.created_at) >= cutoff,
            )
        )
    )
    if own_recent_results:
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

    if cache_source_trip_id is not None:
        cached_source_offers = list(
            await session.scalars(
                select(FlightSearchResult).where(
                    col(FlightSearchResult.trip_request_id) == cache_source_trip_id
                )
            )
        )
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
        return results, None

    outcome = await provider.search_offers(
        trip.origin, trip.destination_airport, trip.depart_date, trip.return_date
    )
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
    return results, outcome.unavailable_reason


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
) -> tuple[AgentRun | None, list[AgentRunStep], list[ExecutionEvent]]:
    """The latest agent run (if `/plan` has ever executed for this trip) plus its ordered steps,
    and the trip's full ExecutionEvent timeline (tool/API calls recorded during that run)."""
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")

    agent_run = await session.scalar(
        select(AgentRun)
        .where(col(AgentRun.trip_request_id) == trip_id)
        .order_by(col(AgentRun.started_at).desc())
        .limit(1)
    )
    steps: list[AgentRunStep] = []
    if agent_run is not None:
        steps = list(
            await session.scalars(
                select(AgentRunStep)
                .where(col(AgentRunStep.agent_run_id) == agent_run.id)
                .order_by(col(AgentRunStep.seq))
            )
        )

    events = list(
        await session.scalars(
            select(ExecutionEvent)
            .where(col(ExecutionEvent.trip_request_id) == trip_id)
            .order_by(col(ExecutionEvent.seq))
        )
    )
    return agent_run, steps, events
