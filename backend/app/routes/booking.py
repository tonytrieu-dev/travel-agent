"""HITL booking routes. Each handler stays thin: call the repository (which owns the state
machine + audit), then shape the ORM row into the response model. Domain rejections raise
BookingError, rendered as a ProblemDetail by the app-level handler in main.py.
"""


from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.slack_hitl import notify_pending_approval
from app.config import get_settings
from app.db import get_session
from app.dbos_runtime import execute_booking_durable
from app.models import (
    BookingTransition,
    ConnectorSetting,
    FlightSearchResult,
    HITLBookingLog,
    TripRequest,
)
from app.repositories import booking_repository as repository
from app.schemas import (
    BookingLogOut,
    BookingRequestCreate,
    BookingTransitionOut,
    ProblemDetail,
)

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
    await _notify_slack_if_enabled(session, booking)
    return _to_out(booking)


async def _notify_slack_if_enabled(session: AsyncSession, booking: HITLBookingLog) -> None:
    settings = get_settings()
    if not (
        settings.slack_bot_token
        and settings.slack_signing_secret
        and settings.slack_approvals_channel_id
    ):
        return
    connector_row = await session.scalar(select(ConnectorSetting))
    if connector_row is None or not connector_row.slack_enabled:
        return
    trip = await session.get(TripRequest, booking.trip_request_id)
    flight = await session.get(FlightSearchResult, booking.flight_search_result_id)
    assert trip is not None and flight is not None, (
        "request_booking already validated these exist"
    )
    await notify_pending_approval(settings, booking, trip, flight)


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
async def execute_booking(log_id: int) -> BookingLogOut:
    return await execute_booking_durable(log_id)


@router.post(
    "/bookings/{log_id}/cancel", response_model=BookingLogOut, responses=_NOT_FOUND_OR_CONFLICT
)
async def cancel_booking(
    log_id: int, session: AsyncSession = Depends(get_session)
) -> BookingLogOut:
    booking = await repository.cancel_booking(session, log_id)
    return _to_out(booking)
