"""Deterministic output-validator guardrails on the planner agent: matching activity intensity
to a traveler's declared fitness level used to be prompt-only guidance (the model's judgment
call, no different from "please don't hallucinate"). These tests guard the structural rejection
that replaced it — same reasoning as the existing citation-grounding validator.
"""


from typing import Literal

import pytest
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.agent.planner import (
    PlannerDeps,
    reject_flight_activities,
    reject_optional_clarification,
    reject_unsafe_intensity,
)
from app.models import FitnessLevel
from app.schemas import ActivityOut, ClarificationOut, ItineraryDayOut, ItineraryOut


def _context(fitness_level: FitnessLevel | None) -> RunContext[PlannerDeps]:
    deps = PlannerDeps(flight_provider=None, activity_provider=None, fitness_level=fitness_level)  # type: ignore[arg-type]
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def _itinerary(
    intensity: Literal["low", "moderate", "high"],
    name: str = "Summit hike",
    description: str = "A steep trail.",
) -> ItineraryOut:
    return ItineraryOut(
        days=[
            ItineraryDayOut(
                day_number=1,
                summary="Explore",
                activities=[
                    ActivityOut(
                        name=name,
                        description=description,
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
        reject_unsafe_intensity(ctx, _itinerary("high"))


def test_allows_moderate_intensity_activity_for_low_fitness_traveler() -> None:
    ctx = _context(FitnessLevel.LOW)

    result = reject_unsafe_intensity(ctx, _itinerary("moderate"))

    assert isinstance(result, ItineraryOut)
    assert result.days[0].activities[0].intensity == "moderate", (
        "moderate intensity must pass through unchanged for a low-fitness traveler — only "
        "'high' is blocked, so the guardrail must not over-reject"
    )


def test_rejects_flight_itself_but_allows_an_unrelated_activity() -> None:
    ctx = _context(FitnessLevel.HIGH)

    with pytest.raises(ModelRetry, match="Outbound flight"):
        reject_flight_activities(
            ctx,
            _itinerary("low", "Outbound flight", "Fly from JFK to SAN."),
        )

    assert isinstance(
        reject_flight_activities(
            ctx,
            _itinerary("low", "City walking tour", "Explore the historic downtown."),
        ),
        ItineraryOut,
    )

    assert isinstance(
        reject_flight_activities(
            ctx,
            _itinerary("low", "USS Midway Museum", "Walk the aircraft carrier's flight deck."),
        ),
        ItineraryOut,
    ), (
        "a real activity that only mentions flying in its description must not be rejected — "
        "scanning the description false-positives and traps the model in a retry loop"
    )


def test_rejects_optional_clarification_when_trip_inputs_are_complete() -> None:
    ctx = _context(FitnessLevel.HIGH)

    with pytest.raises(ModelRetry, match="Plan directly"):
        reject_optional_clarification(ctx, ClarificationOut(questions=["What is your budget?"]))

    assert isinstance(
        reject_optional_clarification(
            ctx,
            ClarificationOut(questions=["Which Springfield destination did you mean?"]),
        ),
        ClarificationOut,
    )
