from copy import deepcopy
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_evals.evaluators import (
    EvaluationReason,
    EvaluatorContext,
    ReportEvaluatorContext,
)

from app.config import MAX_TOOL_STEPS
from app.models import FitnessLevel
from app.schemas import ActivityOut, ClarificationOut, ItineraryDayOut, ItineraryOut
from evals.evaluators import (
    PLANNER_TRACE_ATTRIBUTE,
    CaseMetadata,
    CitationGrounding,
    FlightSearchTrajectory,
    LowFitnessSafety,
    NoFlightActivities,
    OutputTypeMatches,
    PhysicalLoad,
    PhysicalLoadComparisons,
    PlannerTrace,
    ToolCallBudget,
    WebSearchTrajectory,
    extract_planner_trace,
)

SOURCE_URL = "https://example.test/museum"


def _metadata() -> CaseMetadata:
    return {
        "expects": "itinerary",
        "age": 24,
        "fitness_level": FitnessLevel.HIGH,
        "flight_search": {
            "departure_id": "JFK",
            "arrival_id": "SAN",
            "outbound_date": "2026-09-01",
            "return_date": "2026-09-08",
        },
    }


def _itinerary(source_url: str = SOURCE_URL, intensity: str = "low") -> ItineraryOut:
    return ItineraryOut(
        days=[
            ItineraryDayOut(
                day_number=1,
                summary="Museum day",
                activities=[
                    ActivityOut(
                        name="Museum",
                        description="Visit the museum.",
                        intensity=intensity,
                        source_url=source_url,
                    )
                ],
            )
        ]
    )


def _good_trace() -> PlannerTrace:
    flight_search = _metadata()["flight_search"]
    assert flight_search is not None
    return {
        "calls": [
            {
                "name": "search_flights",
                "arguments": dict(flight_search),
                "status": "success",
                "result_count": 1,
                "result_urls": [],
            },
            {
                "name": "web_search",
                "arguments": {"query": "things to do in San Diego", "max_results": 3},
                "status": "success",
                "result_count": 1,
                "result_urls": [SOURCE_URL],
            },
        ],
        "tool_call_count": 2,
        "valid": True,
    }


def _context(
    trace: PlannerTrace | None = None,
    *,
    output: ItineraryOut | ClarificationOut | None = None,
    metadata: CaseMetadata | None = None,
) -> EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]:
    attributes = {} if trace is None else {PLANNER_TRACE_ATTRIBUTE: trace}
    return cast(
        EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata],
        SimpleNamespace(
            inputs="prompt",
            output=output or _itinerary(),
            metadata=metadata or _metadata(),
            attributes=attributes,
        ),
    )


def _passes(
    evaluator: FlightSearchTrajectory
    | WebSearchTrajectory
    | ToolCallBudget
    | CitationGrounding,
    trace: PlannerTrace,
) -> bool:
    return bool(evaluator.evaluate(_context(trace)).value)


def test_extract_planner_trace_pairs_results_and_ignores_output_tools() -> None:
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_flights",
                    '{"departure_id":"JFK","arrival_id":"SAN","outbound_date":"2026-09-01",'
                    '"return_date":"2026-09-08"}',
                    "flight-1",
                ),
                ToolCallPart(
                    "web_search", {"query": "things to do in San Diego", "max_results": 3}, "web-1"
                ),
                ToolCallPart("final_result_ItineraryOut", {}, "output-1"),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "web_search",
                    [{"title": "Museum", "url": SOURCE_URL, "content": "Details"}],
                    "web-1",
                ),
                ToolReturnPart(
                    "search_flights",
                    {"offers": [{"carrier": "Test Air"}], "unavailable_reason": None},
                    "flight-1",
                ),
            ]
        ),
        ModelResponse(parts=[ToolCallPart("web_search", '{"query":"San Diego parks"}', "web-2")]),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    "temporary failure", tool_name="web_search", tool_call_id="web-2"
                )
            ]
        ),
    ]

    trace = extract_planner_trace(messages)

    assert [call["name"] for call in trace["calls"]] == [
        "search_flights",
        "web_search",
        "web_search",
    ]
    assert [call["status"] for call in trace["calls"]] == ["success", "success", "retry"]
    assert trace["calls"][0]["arguments"]["return_date"] == "2026-09-08"
    assert trace["calls"][0]["result_count"] == 1
    assert trace["calls"][1]["result_urls"] == [SOURCE_URL]
    assert trace["tool_call_count"] == 3
    assert trace["valid"]


def test_good_trace_passes_deterministic_trajectory_evaluators() -> None:
    trace = _good_trace()

    assert _passes(FlightSearchTrajectory(), trace)
    assert _passes(WebSearchTrajectory(), trace)
    assert _passes(ToolCallBudget(), trace)
    assert _passes(CitationGrounding(), trace)


def test_flight_search_requires_one_successful_call_with_exact_arguments() -> None:
    wrong_arguments = _good_trace()
    wrong_arguments["calls"][0]["arguments"]["return_date"] = "2026-09-09"
    duplicate = _good_trace()
    duplicate["calls"].append(deepcopy(duplicate["calls"][0]))
    duplicate["tool_call_count"] += 1

    assert not _passes(FlightSearchTrajectory(), wrong_arguments)
    assert not _passes(FlightSearchTrajectory(), duplicate)


def test_web_search_enforces_budget_success_and_no_flight_fact_queries() -> None:
    no_search = _good_trace()
    no_search["calls"] = no_search["calls"][:1]
    no_search["tool_call_count"] = 1
    too_many = _good_trace()
    too_many["calls"].extend([deepcopy(too_many["calls"][1]) for _ in range(3)])
    too_many["tool_call_count"] = 5
    flight_query = _good_trace()
    flight_query["calls"][1]["arguments"]["query"] = "JFK SAN prices"
    narrow_query = _good_trace()
    narrow_query["calls"][1]["arguments"]["query"] = "Balboa Park opening hours"
    retried = _good_trace()
    retried["calls"][1]["status"] = "retry"

    for trace in (no_search, too_many, flight_query, narrow_query, retried):
        assert not _passes(WebSearchTrajectory(), trace)


def test_tool_budget_grounding_and_output_type_fail_closed() -> None:
    over_budget = _good_trace()
    over_budget["calls"].extend(
        deepcopy(over_budget["calls"][1])
        for _ in range(MAX_TOOL_STEPS + 1 - len(over_budget["calls"]))
    )
    over_budget["tool_call_count"] = len(over_budget["calls"])

    assert not _passes(ToolCallBudget(), over_budget)
    assert not CitationGrounding().evaluate(_context()).value
    assert not CitationGrounding().evaluate(
        _context(_good_trace(), output=_itinerary("https://invented.test"))
    ).value
    assert not OutputTypeMatches().evaluate(
        _context(_good_trace(), output=ClarificationOut(questions=["Which San Diego?"]))
    ).value


def test_malformed_or_mismatched_trace_evidence_fails_closed() -> None:
    messages = [
        ModelRequest(
            parts=[ToolReturnPart("web_search", [], "orphan")]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "search_flights",
                    '{"departure_id":"JFK","arrival_id":"SAN","outbound_date":"2026-09-01",'
                    '"return_date":"2026-09-08"}',
                    "shared",
                )
            ]
        ),
        ModelRequest(parts=[ToolReturnPart("web_search", {}, "shared")]),
    ]

    trace = extract_planner_trace(messages)

    assert not trace["valid"]
    assert not _passes(FlightSearchTrajectory(), trace)
    assert not _passes(ToolCallBudget(), trace)


def test_physical_load_rejects_unknown_and_unsafe_low_fitness_intensity() -> None:
    unknown = PhysicalLoad().evaluate(_context(output=_itinerary(intensity="gentle")))
    low_metadata = _metadata()
    low_metadata["fitness_level"] = FitnessLevel.LOW
    unsafe = LowFitnessSafety().evaluate(
        _context(output=_itinerary(intensity="strenuous"), metadata=low_metadata)
    )
    flight = NoFlightActivities().evaluate(
        _context(
            output=ItineraryOut(
                days=[
                    ItineraryDayOut(
                        day_number=1,
                        summary="Travel",
                        activities=[
                            ActivityOut(
                                name="Flight to San Diego",
                                description="Board the plane.",
                                intensity="low",
                                source_url=SOURCE_URL,
                            )
                        ],
                    )
                ]
            )
        )
    )

    known_intensity = unknown["known_intensity"]
    assert isinstance(known_intensity, EvaluationReason)
    assert not known_intensity.value
    assert not unsafe.value
    assert not flight.value


def test_report_comparison_uses_mean_load_and_rejects_regression() -> None:
    cases = []
    for age, fitness_level, load in (
        (24, FitnessLevel.LOW, 1),
        (24, FitnessLevel.LOW, 3),
        (24, FitnessLevel.HIGH, 2),
        (78, FitnessLevel.LOW, 1),
        (78, FitnessLevel.HIGH, 2),
    ):
        metadata = _metadata()
        metadata["age"] = age
        metadata["fitness_level"] = fitness_level
        cases.append(SimpleNamespace(name="case", metadata=metadata, metrics={"physical_load": load}))
    context = cast(
        ReportEvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata],
        SimpleNamespace(report=SimpleNamespace(cases=cases)),
    )

    table = PhysicalLoadComparisons().evaluate(context)

    assert len(table.rows) == 4

    cases[2].metrics["physical_load"] = 1
    with pytest.raises(ValueError):
        PhysicalLoadComparisons().evaluate(context)


def test_dataset_has_four_matched_cases_and_all_deterministic_evaluators() -> None:
    from evals.dataset import dataset

    assert {
        (case.metadata["age"], case.metadata["fitness_level"])
        for case in dataset.cases
        if case.metadata is not None
    } == {
        (24, FitnessLevel.LOW),
        (24, FitnessLevel.HIGH),
        (78, FitnessLevel.LOW),
        (78, FitnessLevel.HIGH),
    }
    assert {
        type(evaluator)
        for evaluator in dataset.evaluators
    }.issuperset(
        {
            OutputTypeMatches,
            CitationGrounding,
            FlightSearchTrajectory,
            WebSearchTrajectory,
            ToolCallBudget,
            PhysicalLoad,
            LowFitnessSafety,
        }
    )
