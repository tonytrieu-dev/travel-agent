"""Deterministic output-validator guardrails on the planner agent: matching activity intensity
to a traveler's declared fitness level used to be prompt-only guidance (the model's judgment
call, no different from "please don't hallucinate"). These tests guard the structural rejection
that replaced it — same reasoning as the existing citation-grounding validator.
"""

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.agent.planner import PlannerDeps, reject_unsafe_intensity
from app.models import FitnessLevel
from app.schemas import ActivityOut, ItineraryDayOut, ItineraryOut


def _context(fitness_level: FitnessLevel | None) -> RunContext[PlannerDeps]:
    deps = PlannerDeps(flight_provider=None, activity_provider=None, fitness_level=fitness_level)  # type: ignore[arg-type]
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def _itinerary(intensity: str) -> ItineraryOut:
    return ItineraryOut(
        days=[
            ItineraryDayOut(
                day_number=1,
                summary="Explore",
                activities=[
                    ActivityOut(
                        name="Summit hike",
                        description="A steep trail.",
                        intensity=intensity,
                        source_url="https://example.test/hike",
                    )
                ],
            )
        ]
    )


def test_rejects_strenuous_activity_for_low_fitness_traveler() -> None:
    ctx = _context(FitnessLevel.LOW)

    with pytest.raises(ModelRetry, match="Summit hike"):
        reject_unsafe_intensity(ctx, _itinerary("STRENUOUS"))


def test_allows_moderate_intensity_activity_for_low_fitness_traveler() -> None:
    ctx = _context(FitnessLevel.LOW)

    result = reject_unsafe_intensity(ctx, _itinerary("moderate"))

    assert isinstance(result, ItineraryOut)
    assert result.days[0].activities[0].intensity == "moderate", (
        "moderate intensity must pass through unchanged for a low-fitness traveler — only "
        "'high' is blocked, so the guardrail must not over-reject"
    )
