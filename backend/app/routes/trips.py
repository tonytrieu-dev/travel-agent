"""Trip routes. Each handler stays thin: call the repository (which owns validation, caching,
and itinerary persistence), then shape the result into the response model. Domain rejections
raise TripError, rendered as a ProblemDetail by the app-level handler in main.py.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.flights_searchapi import get_flight_provider
from app.config import (
    GEMINI_INPUT_PRICE_PER_MILLION_TOKENS,
    GEMINI_OUTPUT_PRICE_PER_MILLION_TOKENS,
    MAX_CONTEXT_TOKENS,
    get_settings,
)
from app.db import get_session
from app.dbos_runtime import run_planner_durable
from app.dependencies import get_current_user
from app.models import AgentRun, AgentRunStep, ExecutionEvent, User
from app.rate_limit import enforce_request_rate_limit
from app.repositories import trips_repository as repository
from app.schemas import (
    AgentRunOut,
    AgentRunStepOut,
    ClarificationOut,
    ExecutionEventOut,
    ExecutionPanelOut,
    FlightOfferOut,
    FlightSearchOut,
    PlanNeedsClarificationOut,
    PlanOut,
    PlanReadyOut,
    ProblemDetail,
    TripRequestCreate,
    TripRequestOut,
    TripRequestUpdate,
)

router = APIRouter(prefix="/api", tags=["trips"])

_VALIDATION: dict[int | str, dict[str, Any]] = {422: {"model": ProblemDetail}}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ProblemDetail}}
_NOT_FOUND_OR_VALIDATION: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}
_NOT_FOUND_OR_RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetail},
    429: {"model": ProblemDetail},
}


@router.post("/trips", response_model=TripRequestOut, responses=_VALIDATION)
async def create_trip(
    body: TripRequestCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TripRequestOut:
    assert user.id is not None, "get_current_user must always return a persisted user"
    trip = await repository.create_trip(session, user.id, body)
    return TripRequestOut.model_validate(trip)


@router.patch(
    "/trips/{trip_id}", response_model=TripRequestOut, responses=_NOT_FOUND_OR_VALIDATION
)
async def update_trip(
    trip_id: int, body: TripRequestUpdate, session: AsyncSession = Depends(get_session)
) -> TripRequestOut:
    trip = await repository.update_trip(session, trip_id, body)
    return TripRequestOut.model_validate(trip)


@router.post(
    "/trips/{trip_id}/flights/search",
    response_model=FlightSearchOut,
    responses=_NOT_FOUND_OR_RATE_LIMITED,
    dependencies=[Depends(enforce_request_rate_limit)],
)
async def search_trip_flights(
    trip_id: int, session: AsyncSession = Depends(get_session)
) -> FlightSearchOut:
    provider = get_flight_provider(get_settings())
    offers, unavailable_reason = await repository.search_flights(session, trip_id, provider)
    return FlightSearchOut(
        offers=[FlightOfferOut.model_validate(offer) for offer in offers],
        unavailable_reason=unavailable_reason,
    )


@router.post(
    "/trips/{trip_id}/plan",
    response_model=PlanOut,
    responses=_NOT_FOUND_OR_RATE_LIMITED,
    dependencies=[Depends(enforce_request_rate_limit)],
)
async def plan_trip(trip_id: int, session: AsyncSession = Depends(get_session)) -> PlanOut:
    output = await repository.get_or_create_itinerary(session, trip_id, run_planner_durable)
    if isinstance(output, ClarificationOut):
        return PlanNeedsClarificationOut(questions=output.questions)
    return PlanReadyOut(itinerary=output)


def _to_panel_out(
    trip_id: int,
    agent_run: AgentRun | None,
    steps: list[AgentRunStep],
    events: list[ExecutionEvent],
) -> ExecutionPanelOut:
    if agent_run is None:
        return ExecutionPanelOut(
            trip_request_id=trip_id,
            events=[ExecutionEventOut.model_validate(event) for event in events],
        )

    estimated_cost_usd = (
        agent_run.total_input_tokens * GEMINI_INPUT_PRICE_PER_MILLION_TOKENS
        + agent_run.total_output_tokens * GEMINI_OUTPUT_PRICE_PER_MILLION_TOKENS
    ) / 1_000_000
    budget_utilization_pct = (
        100 * (agent_run.total_input_tokens + agent_run.total_output_tokens) / MAX_CONTEXT_TOKENS
    )
    agent_run_out = AgentRunOut.model_validate(agent_run)
    agent_run_out.steps = [AgentRunStepOut.model_validate(step) for step in steps]
    return ExecutionPanelOut(
        trip_request_id=trip_id,
        agent_run=agent_run_out,
        events=[ExecutionEventOut.model_validate(event) for event in events],
        estimated_cost_usd=round(estimated_cost_usd, 6),
        budget_utilization_pct=round(budget_utilization_pct, 2),
    )


@router.get("/trips/{trip_id}/execution", response_model=ExecutionPanelOut, responses=_NOT_FOUND)
async def get_trip_execution(
    trip_id: int, session: AsyncSession = Depends(get_session)
) -> ExecutionPanelOut:
    agent_run, steps, events = await repository.get_execution_panel(session, trip_id)
    return _to_panel_out(trip_id, agent_run, steps, events)
