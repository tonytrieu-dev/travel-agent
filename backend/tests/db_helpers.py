"""Direct-DB helpers for tests: seed a booking into a precise state and read booking facts back.

``run_db`` runs each unit of work on its own short-lived engine/loop so seed connections never
share the app's portal event loop (see conftest for why that separation matters).
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import col

from app.models import (
    BookingTransition,
    FlightSearchResult,
    HITLBookingLog,
    TripRequest,
    User,
    utcnow,
)
from app.state import BookingState

TEST_DATABASE_URL = "postgresql+asyncpg://tony@localhost:5432/travel_agent_test"

_ResultT = TypeVar("_ResultT")


def run_db(work: Callable[[AsyncSession], Awaitable[_ResultT]]) -> _ResultT:
    async def _runner() -> _ResultT:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    import asyncio

    return asyncio.run(_runner())


async def seed_booking(
    session: AsyncSession, *, state: BookingState, expires_in_minutes: int
) -> int:
    """Insert a full trip→flight→booking chain and return the booking log id."""
    user = User()
    session.add(user)
    await session.flush()
    assert user.id is not None

    trip = TripRequest(
        user_id=user.id,
        origin="JFK",
        destination="Paris",
        destination_airport="CDG",
        depart_date="2026-08-01",
    )
    session.add(trip)
    await session.flush()
    assert trip.id is not None

    flight = FlightSearchResult(
        trip_request_id=trip.id,
        offer_index=0,
        carrier="AF",
        price_usd=512.0,
        currency="USD",
        depart_at="2026-08-01T09:00:00",
        arrive_at="2026-08-01T21:30:00",
        stops=0,
        booking_token="tok-abc",
        raw_offer={"price": 512.0},
    )
    session.add(flight)
    await session.flush()
    assert flight.id is not None

    confirmed_at = utcnow() if state is BookingState.CONFIRMED else None
    booking = HITLBookingLog(
        trip_request_id=trip.id,
        flight_search_result_id=flight.id,
        state=state,
        requested_by_user_id=user.id,
        expires_at=utcnow() + timedelta(minutes=expires_in_minutes),
        confirmed_at=confirmed_at,
    )
    session.add(booking)
    await session.flush()
    assert booking.id is not None
    return booking.id


async def get_booking(session: AsyncSession, log_id: int) -> HITLBookingLog:
    booking = await session.get(HITLBookingLog, log_id)
    assert booking is not None, f"booking log {log_id} vanished from the DB"
    return booking


async def count_transitions_into(session: AsyncSession, log_id: int, to_state: BookingState) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(BookingTransition)
        .where(
            col(BookingTransition.booking_log_id) == log_id,
            col(BookingTransition.to_state) == to_state,
        )
    )
    return result.scalar_one()
