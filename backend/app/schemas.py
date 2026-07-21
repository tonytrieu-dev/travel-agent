"""API request/response models — the strictly-typed boundary between HTTP and the domain.

These mirror the authored contract in specs/openapi.yaml; test_openapi_contract asserts the
runtime schema FastAPI generates from them stays in sync with that file.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AgentStepKind, ExecutionEventKind, FitnessLevel, TripStatus
from app.state import BookingState


class ErrorCode(StrEnum):
    BOOKING_NOT_FOUND = "booking_not_found"
    TRIP_NOT_FOUND = "trip_not_found"
    FLIGHT_NOT_FOUND = "flight_not_found"
    BOOKING_EXPIRED = "booking_expired"
    INVALID_TRANSITION = "invalid_transition"
    BOOKING_OPTIONS_UNAVAILABLE = "booking_options_unavailable"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"


def validate_trip_dates(depart_date: str, return_date: str | None) -> None:
    """Shared by TripRequestCreate and the trip-update repository path, so a PATCH that only
    changes one of the two dates is checked against the same rule as trip creation."""
    if date.fromisoformat(depart_date) < date.today():
        raise ValueError(f"depart_date {depart_date} is in the past.")
    if return_date is not None and date.fromisoformat(return_date) < date.fromisoformat(
        depart_date
    ):
        raise ValueError(f"return_date {return_date} is before depart_date {depart_date}.")


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


class TripRequestCreate(BaseModel):
    origin: str
    destination: str
    destination_airport: str
    depart_date: str
    return_date: str | None = None
    age: int | None = None
    fitness_level: FitnessLevel | None = None
    budget_usd: float | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> "TripRequestCreate":
        validate_trip_dates(self.depart_date, self.return_date)
        return self


class TripRequestUpdate(BaseModel):
    origin: str | None = None
    destination: str | None = None
    destination_airport: str | None = None
    depart_date: str | None = None
    return_date: str | None = None
    age: int | None = None
    fitness_level: FitnessLevel | None = None
    budget_usd: float | None = None


class TripRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    origin: str
    destination: str
    destination_airport: str
    depart_date: str
    return_date: str | None = None
    age: int | None = None
    fitness_level: FitnessLevel | None = None
    budget_usd: float | None = None
    status: TripStatus
    created_at: datetime


class FlightOfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    offer_index: int
    carrier: str
    price_usd: float
    currency: str
    depart_at: str
    arrive_at: str
    stops: int
    source: str


class FlightSearchOut(BaseModel):
    offers: list[FlightOfferOut]
    unavailable_reason: str | None = None


class PlanReadyOut(BaseModel):
    status: Literal["ready"] = "ready"
    itinerary: ItineraryOut


class PlanNeedsClarificationOut(BaseModel):
    status: Literal["needs_clarification"] = "needs_clarification"
    questions: list[str]


PlanOut = Annotated[
    PlanReadyOut | PlanNeedsClarificationOut, Field(discriminator="status")
]


class AgentRunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    kind: AgentStepKind
    name: str
    status: str
    duration_ms: int | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    tokens: int | None = None


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    model: str
    total_input_tokens: int
    total_output_tokens: int
    total_ms: int
    started_at: datetime
    finished_at: datetime | None = None
    steps: list[AgentRunStepOut] = []


class ExecutionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    kind: ExecutionEventKind
    name: str
    status: str
    detail: str
    duration_ms: int | None = None
    created_at: datetime


class ExecutionPanelOut(BaseModel):
    trip_request_id: int
    agent_run: AgentRunOut | None = None
    events: list[ExecutionEventOut] = []
    estimated_cost_usd: float | None = None
    budget_utilization_pct: float | None = None
