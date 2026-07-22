"""The eval dataset for the travel-planner agent: fitness-appropriate itineraries and citation
grounding (no hallucinated activities). Age and fitness level are mandatory at trip intake now,
so there's no "ask for clarification" case here — the agent is never handed a prompt lacking them.
"""

from pydantic_evals import Case, Dataset

from app.schemas import ClarificationOut, ItineraryOut
from evals.evaluators import (
    CaseMetadata,
    CitationGrounding,
    OutputTypeMatches,
    build_fitness_appropriateness_judge,
)

_TRIP_PROMPT = "Plan a trip from JFK to CDG, departing 2026-09-01, returning 2026-09-08."


def _build_cases() -> list[Case[str, ItineraryOut | ClarificationOut, CaseMetadata]]:
    fitness_appropriateness_judge = build_fitness_appropriateness_judge()
    return [
        Case(
            name="young_high_fitness_gets_active_itinerary",
            inputs=f"{_TRIP_PROMPT} Traveler age: 24. Fitness level: high.",
            metadata=CaseMetadata(expects="itinerary"),
            evaluators=(fitness_appropriateness_judge, CitationGrounding()),
        ),
        Case(
            name="elderly_low_fitness_gets_gentle_itinerary",
            inputs=f"{_TRIP_PROMPT} Traveler age: 78. Fitness level: low.",
            metadata=CaseMetadata(expects="itinerary"),
            evaluators=(fitness_appropriateness_judge, CitationGrounding()),
        ),
    ]


dataset: Dataset[str, ItineraryOut | ClarificationOut, CaseMetadata] = Dataset(
    name="travel_planner_evals",
    cases=_build_cases(),
    evaluators=[OutputTypeMatches()],
)
