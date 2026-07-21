"""HITL booking routes. Each handler stays thin: call the repository (which owns the state
machine + audit), then shape the ORM row into the response model. Domain rejections raise
BookingError, rendered as a ProblemDetail by the app-level handler in main.py.
"""


from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.flights_searchapi import get_flight_provider
from app.config import get_settings
from app.db import get_session
from app.models import BookingTransition, FlightSearchResult, HITLBookingLog
from app.repositories import booking_repository as repository
from app.repositories.booking_repository import BookingOptionsFetcher
from app.schemas import BookingLogOut, BookingRequestCreate, BookingTransitionOut, ProblemDetail

router = APIRouter(prefix="/api", tags=["booking"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ProblemDetail}}
_NOT_FOUND_OR_CONFLICT: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
}
_EXECUTE_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    502: {"model": ProblemDetail},
}


def get_booking_options_fetcher() -> BookingOptionsFetcher:
    """The Strategy-selected flight provider's real booking-options call — live SearchApi or a
    replayed cassette per ``USE_LIVE_FLIGHT_API``. Tests override this dependency with a
    counting spy so they never hit the real API or a cassette file."""
    provider = get_flight_provider(get_settings())

    async def _fetch(flight_search_result: FlightSearchResult) -> list[dict]:
        return await provider.fetch_booking_options(flight_search_result.booking_token)

    return _fetch


def _to_out(
    booking: HITLBookingLog, transitions: list[BookingTransition] | None = None
) -> BookingLogOut:
    out = BookingLogOut.model_validate(booking)
    if transitions is not None:
        out.transitions = [BookingTransitionOut.model_validate(t) for t in transitions]
    return out


@router.post(
    "/trips/{trip_id}/booking/request", response_model=BookingLogOut, responses=_NOT_FOUND
)
async def request_booking(
    trip_id: int,
    body: BookingRequestCreate,
    session: AsyncSession = Depends(get_session),
) -> BookingLogOut:
    booking = await repository.request_booking(session, trip_id, body.flight_search_result_id)
    return _to_out(booking)


@router.get("/bookings/{log_id}", response_model=BookingLogOut, responses=_NOT_FOUND)
async def get_booking(
    log_id: int, session: AsyncSession = Depends(get_session)
) -> BookingLogOut:
    booking, transitions = await repository.get_booking_with_transitions(session, log_id)
    return _to_out(booking, transitions)


@router.post(
    "/bookings/{log_id}/confirm", response_model=BookingLogOut, responses=_NOT_FOUND_OR_CONFLICT
)
async def confirm_booking(
    log_id: int, session: AsyncSession = Depends(get_session)
) -> BookingLogOut:
    booking = await repository.confirm_booking(session, log_id)
    return _to_out(booking)


@router.post(
    "/bookings/{log_id}/execute", response_model=BookingLogOut, responses=_EXECUTE_RESPONSES
)
async def execute_booking(
    log_id: int,
    session: AsyncSession = Depends(get_session),
    fetch_options: BookingOptionsFetcher = Depends(get_booking_options_fetcher),
) -> BookingLogOut:
    booking = await repository.execute_booking(session, log_id, fetch_options)
    return _to_out(booking)


@router.post(
    "/bookings/{log_id}/cancel", response_model=BookingLogOut, responses=_NOT_FOUND_OR_CONFLICT
)
async def cancel_booking(
    log_id: int, session: AsyncSession = Depends(get_session)
) -> BookingLogOut:
    booking = await repository.cancel_booking(session, log_id)
    return _to_out(booking)
