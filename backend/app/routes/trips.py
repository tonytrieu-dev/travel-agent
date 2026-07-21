"""Trip routes. Each handler stays thin: call the repository (which owns validation, caching,
and itinerary persistence), then shape the result into the response model. Domain rejections
raise TripError, rendered as a ProblemDetail by the app-level handler in main.py.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.flights_searchapi import get_flight_provider
from app.config import get_settings
from app.db import get_session
from app.dbos_runtime import run_planner_durable
from app.dependencies import get_current_user
from app.models import User
from app.repositories import trips_repository as repository
from app.schemas import (
    ClarificationOut,
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
    "/trips/{trip_id}/flights/search", response_model=FlightSearchOut, responses=_NOT_FOUND
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


@router.post("/trips/{trip_id}/plan", response_model=PlanOut, responses=_NOT_FOUND)
async def plan_trip(trip_id: int, session: AsyncSession = Depends(get_session)) -> PlanOut:
    output = await repository.get_or_create_itinerary(session, trip_id, run_planner_durable)
    if isinstance(output, ClarificationOut):
        return PlanNeedsClarificationOut(questions=output.questions)
    return PlanReadyOut(itinerary=output)
