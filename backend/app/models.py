"""SQLModel tables: 5 core + 4 audit/observability.

Two of the audit tables (``BookingTransition``, ``ExecutionEvent``) are made physically
append-only by database triggers created in the Alembic migration — no application code path,
and no agent, can edit or delete a row once written.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.state import BookingState


def utcnow() -> datetime:
    # Naive UTC: the columns are TIMESTAMP WITHOUT TIME ZONE, so an aware datetime fails to insert.
    return datetime.now(UTC).replace(tzinfo=None)


class FitnessLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class TripStatus(StrEnum):
    CREATED = "created"
    FLIGHTS_SEARCHED = "flights_searched"
    ITINERARY_READY = "itinerary_ready"


class FlightResultSource(StrEnum):
    LIVE = "live"  # fetched live from SearchApi this request
    CACHED = "cached"  # real results reused from an earlier identical search


class ExecutionEventKind(StrEnum):
    API_CALL = "api_call"
    DB_QUERY = "db_query"
    PROTOCOL = "protocol"
    HITL = "hitl"


class AgentStepKind(StrEnum):
    MODEL = "model"
    TOOL = "tool"


# ── Core ─────────────────────────────────────────────────────────────────────────────────


class User(SQLModel, table=True):
    __tablename__ = "user_account"

    id: int | None = Field(default=None, primary_key=True)
    # Nullable so a right-to-erasure request can null the email (anonymize) while leaving the
    # append-only audit rows, which reference user_id only, fully intact.
    email: str | None = Field(default=None, unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class TripRequest(SQLModel, table=True):
    __tablename__ = "trip_request"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user_account.id", index=True)
    origin: str  # IATA code, validated at the API boundary
    destination: str  # free-text city/place name used for activity research
    destination_airport: str  # IATA code for the flight search
    depart_date: str  # ISO date (YYYY-MM-DD)
    return_date: str | None = None
    age: int | None = None  # optional — the agent asks if it needs it
    fitness_level: FitnessLevel | None = None  # optional — the agent asks if it needs it
    budget_usd: float | None = None
    status: TripStatus = Field(default=TripStatus.CREATED)
    created_at: datetime = Field(default_factory=utcnow)


class FlightSearchResult(SQLModel, table=True):
    __tablename__ = "flight_search_result"

    id: int | None = Field(default=None, primary_key=True)
    trip_request_id: int = Field(foreign_key="trip_request.id", index=True)
    offer_index: int
    carrier: str
    price_usd: float
    currency: str
    depart_at: str
    arrive_at: str
    stops: int
    booking_token: str
    raw_offer: dict[str, Any] = Field(sa_column=Column(JSON))  # the real SearchApi offer payload
    source: FlightResultSource = Field(default=FlightResultSource.LIVE)
    created_at: datetime = Field(default_factory=utcnow)


class Itinerary(SQLModel, table=True):
    __tablename__ = "itinerary"
    # One itinerary per trip: the DB blocks a concurrent second generation from burning a second
    # LLM run.
    __table_args__ = (UniqueConstraint("trip_request_id", name="uq_itinerary_trip_request"),)

    id: int | None = Field(default=None, primary_key=True)
    trip_request_id: int = Field(foreign_key="trip_request.id", index=True)
    # days: list of {day_number, summary, activities: [{name, description, intensity,
    # source_url}]} — every activity carries the Tavily source_url that grounds it.
    days: list[dict[str, Any]] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class HITLBookingLog(SQLModel, table=True):
    __tablename__ = "hitl_booking_log"
    # One booking request per (trip, flight offer): a double-click on "Request booking" reuses
    # the existing row instead of creating a duplicate.
    __table_args__ = (
        UniqueConstraint(
            "trip_request_id", "flight_search_result_id", name="uq_booking_trip_flight"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    trip_request_id: int = Field(foreign_key="trip_request.id", index=True)
    flight_search_result_id: int = Field(foreign_key="flight_search_result.id", index=True)
    state: BookingState = Field(default=BookingState.PENDING_USER_CONFIRMATION, index=True)
    requested_by_user_id: int = Field(foreign_key="user_account.id")
    # Our internal record locator (set at EXECUTED) — a genuine reference for OUR system, not a
    # fabricated airline PNR (only an airline can mint a real PNR).
    booking_reference: str | None = None
    # The real SearchApi booking options fetched at execute time: provider, price, and a
    # booking_url to the airline/OTA checkout.
    booking_options: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    expires_at: datetime  # price-staleness TTL
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ── Audit & observability ────────────────────────────────────────────────────────────────


class BookingTransition(SQLModel, table=True):
    """APPEND-ONLY (DB trigger). The tamper-evident proof of who approved what, and when."""

    __tablename__ = "booking_transition"

    id: int | None = Field(default=None, primary_key=True)
    booking_log_id: int = Field(foreign_key="hitl_booking_log.id", index=True)
    from_state: BookingState
    to_state: BookingState
    actor_user_id: int | None = Field(default=None, foreign_key="user_account.id")
    reason: str
    created_at: datetime = Field(default_factory=utcnow)


class ExecutionEvent(SQLModel, table=True):
    """APPEND-ONLY (DB trigger). Every API call / DB query / protocol / HITL step, in order."""

    __tablename__ = "execution_event"

    id: int | None = Field(default=None, primary_key=True)
    trip_request_id: int = Field(foreign_key="trip_request.id", index=True)
    seq: int
    kind: ExecutionEventKind
    name: str
    status: str
    detail: str
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_run"

    id: int | None = Field(default=None, primary_key=True)
    trip_request_id: int = Field(foreign_key="trip_request.id", index=True)
    status: str
    model: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_ms: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class AgentRunStep(SQLModel, table=True):
    __tablename__ = "agent_run_step"

    id: int | None = Field(default=None, primary_key=True)
    agent_run_id: int = Field(foreign_key="agent_run.id", index=True)
    seq: int
    kind: AgentStepKind
    name: str
    status: str
    duration_ms: int | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    tokens: int | None = None
