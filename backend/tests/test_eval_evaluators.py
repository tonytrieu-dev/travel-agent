from copy import deepcopy
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_evals.evaluators import (
    EvaluatorContext,
    ReportEvaluatorContext,
)
from pydantic_evals.reporting import ReportCaseFailure

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


def _itinerary(
    source_url: str = SOURCE_URL, intensity: Literal["low", "moderate", "high"] = "low"
) -> ItineraryOut:
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


def test_flight_search_requires_exactly_one_call_and_rejects_wrong_arguments() -> None:
    """A second search_flights call is wasted tool-call variance, not a legitimate retry — the
    planner already has the trip's route/dates and never needs to search them twice."""
    wrong_arguments = _good_trace()
    wrong_arguments["calls"][0]["arguments"]["return_date"] = "2026-09-09"
    duplicate = _good_trace()
    duplicate["calls"].append(deepcopy(duplicate["calls"][0]))
    duplicate["tool_call_count"] += 1

    assert not _passes(FlightSearchTrajectory(), wrong_arguments)
    assert not _passes(FlightSearchTrajectory(), duplicate)


def test_web_search_requires_exactly_one_successful_broad_non_flight_query() -> None:
    """A second (even legitimate, broad) web_search call is wasted tool-call variance the planner
    doesn't need — one broad query is enough to ground an itinerary in real activities."""
    no_search = _good_trace()
    no_search["calls"] = no_search["calls"][:1]
    no_search["tool_call_count"] = 1
    second_broad_search = _good_trace()
    second_broad_search["calls"].append(deepcopy(second_broad_search["calls"][1]))
    second_broad_search["tool_call_count"] += 1
    flight_query = _good_trace()
    flight_query["calls"][1]["arguments"]["query"] = "JFK SAN prices"
    narrow_query = _good_trace()
    narrow_query["calls"][1]["arguments"]["query"] = "Balboa Park opening hours"
    retried = _good_trace()
    retried["calls"][1]["status"] = "retry"

    for trace in (no_search, second_broad_search, flight_query, narrow_query, retried):
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


def test_activity_out_rejects_intensity_outside_the_closed_vocabulary() -> None:
    """The live eval trace showed the model writing descriptive phrases ("low to moderate (tram
    seated)") that no downstream check could match against a fixed term list — intensity is now
    a closed Literal so that failure is caught at parse time, not silently scored as unknown."""
    with pytest.raises(ValidationError, match="low.*moderate.*high"):
        ActivityOut(
            name="Museum",
            description="Visit the museum.",
            intensity="gentle",  # type: ignore[arg-type]
            source_url=SOURCE_URL,
        )


def test_low_fitness_safety_and_no_flight_activities_reject_correctly() -> None:
    low_metadata = _metadata()
    low_metadata["fitness_level"] = FitnessLevel.LOW
    unsafe = LowFitnessSafety().evaluate(
        _context(output=_itinerary(intensity="high"), metadata=low_metadata)
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
    assert not unsafe.value
    assert not flight.value


def test_report_comparison_reports_regressions_and_missing_samples_without_crashing() -> None:
    cases = []
    for age, fitness_level, load in (
        (24, FitnessLevel.LOW, 1),
        (24, FitnessLevel.LOW, 3),
        (24, FitnessLevel.HIGH, 2),
        (24, FitnessLevel.HIGH, 4),
        (78, FitnessLevel.LOW, 1),
        (78, FitnessLevel.LOW, 1),
        (78, FitnessLevel.HIGH, 2),
        (78, FitnessLevel.HIGH, 2),
    ):
        metadata = _metadata()
        metadata["age"] = age
        metadata["fitness_level"] = fitness_level
        cases.append(
            SimpleNamespace(
                name="case",
                metadata=metadata,
                scores={"physical_load": SimpleNamespace(value=load)},
            )
        )
    context = cast(
        ReportEvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata],
        SimpleNamespace(report=SimpleNamespace(cases=cases, failures=[])),
    )

    table = PhysicalLoadComparisons().evaluate(context)

    assert all(row[-1] is True for row in table.rows)

    cases[0].scores["physical_load"].value = 7
    assert PhysicalLoadComparisons().evaluate(context).rows[0][-1] is False

    cases[0].scores["physical_load"].value = 1
    cases[2].scores.clear()
    missing_sample_table = PhysicalLoadComparisons().evaluate(context)
    assert missing_sample_table.rows[0][-1] is False

    failed_metadata = _metadata()
    failed_metadata["age"] = 24
    failed_metadata["fitness_level"] = FitnessLevel.HIGH
    context.report.failures.append(
        ReportCaseFailure(
            name="failed",
            inputs="prompt",
            metadata=failed_metadata,
            expected_output=None,
            error_message="failed",
            error_stacktrace="failed",
        )
    )
    cases[2].scores["physical_load"] = SimpleNamespace(value=2)
    incomplete_table = PhysicalLoadComparisons().evaluate(context)
    assert incomplete_table.rows[0][-1] is False


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
