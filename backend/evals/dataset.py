from pydantic_evals import Case, Dataset

from app.models import FitnessLevel
from app.schemas import ClarificationOut, ItineraryOut
from evals.evaluators import (
    CaseMetadata,
    CitationGrounding,
    FitnessAppropriateness,
    FlightSearchTrajectory,
    LowFitnessSafety,
    NoFlightActivities,
    OutputTypeMatches,
    PhysicalLoad,
    PhysicalLoadComparisons,
    ToolCallBudget,
    WebSearchTrajectory,
)

_TRIP_PROMPT = (
    "Plan a trip from JFK to San Diego (SAN), departing 2026-09-01, returning 2026-09-08."
)
_FLIGHT_SEARCH = {
    "departure_id": "JFK",
    "arrival_id": "SAN",
    "outbound_date": "2026-09-01",
    "return_date": "2026-09-08",
}


def _build_cases() -> list[Case[str, ItineraryOut | ClarificationOut, CaseMetadata]]:
    return [
        Case(
            name=f"age_{age}_{fitness_level.value}_fitness",
            inputs=(
                f"{_TRIP_PROMPT} Traveler age: {age}. "
                f"Fitness level: {fitness_level.value}."
            ),
            metadata=CaseMetadata(
                expects="itinerary",
                age=age,
                fitness_level=fitness_level,
                flight_search=_FLIGHT_SEARCH,
            ),
        )
        for age in (24, 78)
        for fitness_level in (FitnessLevel.LOW, FitnessLevel.HIGH)
    ]


dataset: Dataset[str, ItineraryOut | ClarificationOut, CaseMetadata] = Dataset(
    name="travel_planner_evals",
    cases=_build_cases(),
    evaluators=[
        OutputTypeMatches(),
        CitationGrounding(),
        FlightSearchTrajectory(),
        WebSearchTrajectory(),
        ToolCallBudget(),
        NoFlightActivities(),
        PhysicalLoad(),
        LowFitnessSafety(),
        FitnessAppropriateness(),
    ],
    report_evaluators=[PhysicalLoadComparisons()],
)
