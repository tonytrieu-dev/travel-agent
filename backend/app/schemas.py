"""API request/response models — the strictly-typed boundary between HTTP and the domain.

These mirror the authored contract in specs/openapi.yaml; test_openapi_contract asserts the
runtime schema FastAPI generates from them stays in sync with that file.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.state import BookingState


class ErrorCode(StrEnum):
    BOOKING_NOT_FOUND = "booking_not_found"
    TRIP_NOT_FOUND = "trip_not_found"
    FLIGHT_NOT_FOUND = "flight_not_found"
    BOOKING_EXPIRED = "booking_expired"
    INVALID_TRANSITION = "invalid_transition"
    BOOKING_OPTIONS_UNAVAILABLE = "booking_options_unavailable"


class ProblemDetail(BaseModel):
    code: ErrorCode
    detail: str


class BookingRequestCreate(BaseModel):
    flight_search_result_id: int


class BookingTransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_state: BookingState
    to_state: BookingState
    reason: str
    actor_user_id: int | None = None
    created_at: datetime


class BookingLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trip_request_id: int
    flight_search_result_id: int
    state: BookingState
    booking_reference: str | None = None
    booking_options: list[dict[str, Any]] | None = None
    expires_at: datetime
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime
    transitions: list[BookingTransitionOut] = []


class ActivityOut(BaseModel):
    name: str
    description: str
    intensity: str
    source_url: str


class ItineraryDayOut(BaseModel):
    day_number: int
    summary: str
    activities: list[ActivityOut]


class ItineraryOut(BaseModel):
    days: list[ItineraryDayOut]


class ClarificationOut(BaseModel):
    questions: list[str]
