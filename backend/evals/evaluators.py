import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from statistics import mean
from typing import Literal, TypedDict, cast

from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
    EvaluatorOutput,
    LLMJudge,
    ReportEvaluator,
    ReportEvaluatorContext,
)
from pydantic_evals.reporting import TableResult

from app.agent.planner import _is_unsafe_intensity
from app.config import GEMINI_JUDGE_MODEL, MAX_TOOL_STEPS, get_settings
from app.models import FitnessLevel
from app.schemas import ClarificationOut, ItineraryOut

PLANNER_TRACE_ATTRIBUTE = "planner_trace"
_PLANNER_TOOLS = {"search_flights", "web_search"}
_FLIGHT_FACT_QUERY = re.compile(r"\b(?:flights?|airfares?|airlines?|fares?)\b", re.IGNORECASE)
_FLIGHT_DETAIL_QUERY = re.compile(
    r"\b(?:arrivals?|departures?|prices?|schedules?|times?)\b", re.IGNORECASE
)
_BROAD_ACTIVITY_QUERY = re.compile(
    r"\b(?:things to do|activities|attractions|sights|travel guide)\b", re.IGNORECASE
)
_INTENSITY_LOAD = {"low": 1, "moderate": 2, "high": 3}


class FlightSearchExpectation(TypedDict):
    departure_id: str
    arrival_id: str
    outbound_date: str
    return_date: str | None


class PlannerToolCall(TypedDict):
    name: Literal["search_flights", "web_search"]
    arguments: dict[str, object]
    status: Literal[
        "success", "failed", "denied", "interrupted", "retry", "missing", "ambiguous"
    ]
    result_count: int | None
    result_urls: list[str]


class PlannerTrace(TypedDict):
    calls: list[PlannerToolCall]
    tool_call_count: int
    valid: bool


class CaseMetadata(TypedDict):
    expects: Literal["clarification", "itinerary"]
    age: int
    fitness_level: FitnessLevel
    flight_search: FlightSearchExpectation | None


def _result_summary(part: ToolReturnPart) -> tuple[int | None, list[str], bool]:
    content = part.content
    if part.tool_name == "search_flights" and isinstance(content, Mapping):
        offers = content.get("offers")
        if isinstance(offers, Sequence) and not isinstance(offers, str | bytes):
            return len(offers), [], True
        return None, [], False
    if (
        part.tool_name != "web_search"
        or not isinstance(content, Sequence)
        or isinstance(content, str | bytes)
    ):
        return None, [], False
    if any(
        not isinstance(result, Mapping) or not isinstance(result.get("url"), str)
        for result in content
    ):
        return None, [], False
    results = cast(Sequence[Mapping[str, object]], content)
    return len(results), [cast(str, result["url"]) for result in results], True


def extract_planner_trace(messages: list[ModelMessage]) -> PlannerTrace:
    calls: list[ToolCallPart] = []
    returns: dict[str, list[ToolReturnPart]] = {}
    retries: Counter[str] = Counter()
    pending: dict[str, ToolCallPart] = {}
    valid = True
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_name in _PLANNER_TOOLS:
                calls.append(part)
                if part.tool_call_id in pending:
                    valid = False
                pending[part.tool_call_id] = part
            elif isinstance(part, ToolReturnPart) and part.tool_name in _PLANNER_TOOLS:
                call = pending.get(part.tool_call_id)
                if (
                    call is None
                    or call.tool_name != part.tool_name
                    or returns.get(part.tool_call_id)
                    or retries[part.tool_call_id]
                ):
                    valid = False
                returns.setdefault(part.tool_call_id, []).append(part)
            elif isinstance(part, RetryPromptPart) and part.tool_name in _PLANNER_TOOLS:
                call = pending.get(part.tool_call_id)
                if (
                    call is None
                    or call.tool_name != part.tool_name
                    or retries[part.tool_call_id]
                    or returns.get(part.tool_call_id)
                ):
                    valid = False
                retries[part.tool_call_id] += 1

    call_ids = Counter(call.tool_call_id for call in calls)
    trace_calls: list[PlannerToolCall] = []
    for call in calls:
        matching_returns = returns.get(call.tool_call_id, [])
        if call_ids[call.tool_call_id] > 1 or len(matching_returns) > 1 or matching_returns and retries[call.tool_call_id]:
            status = "ambiguous"
            result_count, result_urls = None, []
            valid = False
        elif matching_returns:
            returned = matching_returns[0]
            status = returned.outcome
            result_count, result_urls, result_valid = _result_summary(returned)
            valid = valid and result_valid
        elif retries[call.tool_call_id]:
            status = "retry"
            result_count, result_urls = None, []
        else:
            status = "missing"
            result_count, result_urls = None, []
            valid = False
        try:
            arguments = cast(dict[str, object], call.args_as_dict())
        except Exception:
            arguments = {}
            status = "ambiguous"
            valid = False
        trace_calls.append(
            {
                "name": cast(Literal["search_flights", "web_search"], call.tool_name),
                "arguments": arguments,
                "status": status,
                "result_count": result_count,
                "result_urls": result_urls,
            }
        )
    return {"calls": trace_calls, "tool_call_count": len(trace_calls), "valid": valid}


def _trace(
    ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata],
) -> PlannerTrace | None:
    value = ctx.attributes.get(PLANNER_TRACE_ATTRIBUTE)
    if not isinstance(value, dict):
        return None
    calls = value.get("calls")
    tool_call_count = value.get("tool_call_count")
    if (
        value.get("valid") is not True
        or not isinstance(calls, list)
        or not isinstance(tool_call_count, int)
        or tool_call_count != len(calls)
    ):
        return None
    if any(
        not isinstance(call, dict)
        or call.get("name") not in _PLANNER_TOOLS
        or not isinstance(call.get("arguments"), dict)
        or call.get("status")
        not in {"success", "failed", "denied", "interrupted", "retry", "missing", "ambiguous"}
        or (
            call.get("result_count") is not None
            and not isinstance(call.get("result_count"), int)
        )
        or not isinstance(call.get("result_urls"), list)
        or any(not isinstance(url, str) for url in call["result_urls"])
        for call in calls
    ):
        return None
    return cast(PlannerTrace, value)


@dataclass
class OutputTypeMatches(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    """The agent's union output resolved to the member this case's prompt calls for."""

    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        if ctx.metadata is None:
            return EvaluationReason(value=False, reason="case is missing required expects metadata")
        expected_type = ItineraryOut if ctx.metadata["expects"] == "itinerary" else ClarificationOut
        matches = isinstance(ctx.output, expected_type)
        reason = None if matches else f"expected {expected_type.__name__}, got {type(ctx.output).__name__}"
        return EvaluationReason(value=matches, reason=reason)


@dataclass
class CitationGrounding(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    """Every `ActivityOut.source_url` in the output must be a URL the run's `web_search` tool
    actually returned — catches a hallucinated activity/citation the model invented instead of
    grounding in a real search result. A no-op on non-itinerary output (nothing to ground)."""

    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        if not isinstance(ctx.output, ItineraryOut):
            return EvaluationReason(value=True, reason="not an itinerary; nothing to ground")

        trace = _trace(ctx)
        if trace is None:
            return EvaluationReason(value=False, reason="planner_trace is missing or malformed")
        returned_urls = {
            url
            for call in trace["calls"]
            if call["name"] == "web_search" and call["status"] == "success"
            for url in call["result_urls"]
        }
        ungrounded_urls = [
            activity.source_url
            for day in ctx.output.days
            for activity in day.activities
            if activity.source_url not in returned_urls
        ]
        if ungrounded_urls:
            return EvaluationReason(
                value=False,
                reason=f"{len(ungrounded_urls)} source_url(s) not returned by web_search: {ungrounded_urls}",
            )
        return EvaluationReason(value=True)


@dataclass
class NoFlightActivities(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        if not isinstance(ctx.output, ItineraryOut):
            return EvaluationReason(value=True)
        flights = [
            activity.name
            for day in ctx.output.days
            for activity in day.activities
            if _FLIGHT_FACT_QUERY.search(f"{activity.name} {activity.description}")
        ]
        return EvaluationReason(
            value=not flights,
            reason=None if not flights else f"flight listed as activity: {flights}",
        )


@dataclass
class FlightSearchTrajectory(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        if ctx.metadata is None or "flight_search" not in ctx.metadata:
            return EvaluationReason(value=False, reason="case is missing flight_search metadata")
        trace = _trace(ctx)
        if trace is None:
            return EvaluationReason(value=False, reason="planner_trace is missing or malformed")
        calls = [call for call in trace["calls"] if call["name"] == "search_flights"]
        expected = ctx.metadata["flight_search"]
        if expected is None:
            matches = not calls
            return EvaluationReason(
                value=matches,
                reason=None if matches else f"expected no search_flights call, got {len(calls)}",
            )
        if len(calls) != 1:
            return EvaluationReason(
                value=False, reason=f"expected one search_flights call, got {len(calls)}"
            )
        call = calls[0]
        matches = call["status"] == "success" and call["arguments"] == expected
        return EvaluationReason(
            value=matches,
            reason=None
            if matches
            else f"expected successful search_flights args {expected}, got {call}",
        )


@dataclass
class WebSearchTrajectory(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        if ctx.metadata is None:
            return EvaluationReason(value=False, reason="case is missing metadata")
        if ctx.metadata["expects"] != "itinerary":
            return EvaluationReason(value=True, reason="clarification case has no web-search budget")
        trace = _trace(ctx)
        if trace is None:
            return EvaluationReason(value=False, reason="planner_trace is missing or malformed")
        calls = [call for call in trace["calls"] if call["name"] == "web_search"]
        if not 1 <= len(calls) <= 3:
            return EvaluationReason(
                value=False, reason=f"expected 1-3 web_search calls, got {len(calls)}"
            )
        if any(call["status"] != "success" for call in calls):
            return EvaluationReason(value=False, reason="every web_search call must succeed")

        route_codes: set[str] = set()
        expected_flight = ctx.metadata.get("flight_search")
        if expected_flight is not None:
            route_codes = {
                expected_flight["departure_id"].lower(),
                expected_flight["arrival_id"].lower(),
            }
        invalid_queries: list[object] = []
        for call in calls:
            query = call["arguments"].get("query")
            if not isinstance(query, str):
                invalid_queries.append(query)
                continue
            normalized = query.lower()
            if _FLIGHT_FACT_QUERY.search(query) or (
                route_codes
                and (
                    all(re.search(rf"\b{re.escape(code)}\b", normalized) for code in route_codes)
                    or (
                        any(
                            re.search(rf"\b{re.escape(code)}\b", normalized)
                            for code in route_codes
                        )
                        and _FLIGHT_DETAIL_QUERY.search(query)
                    )
                )
            ):
                invalid_queries.append(query)
                continue
            if not _BROAD_ACTIVITY_QUERY.search(query):
                invalid_queries.append(query)
        matches = not invalid_queries
        return EvaluationReason(
            value=matches,
            reason=None if matches else f"web_search query is narrow or about flights: {invalid_queries}",
        )


@dataclass
class ToolCallBudget(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        trace = _trace(ctx)
        if trace is None:
            return EvaluationReason(value=False, reason="planner_trace is missing or malformed")
        count = trace["tool_call_count"]
        matches = count <= MAX_TOOL_STEPS
        return EvaluationReason(
            value=matches,
            reason=None if matches else f"{count} planner tool calls exceed limit {MAX_TOOL_STEPS}",
        )


@dataclass
class PhysicalLoad(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> dict[str, int | float | EvaluationReason]:
        if not isinstance(ctx.output, ItineraryOut):
            return {
                "known_intensity": EvaluationReason(
                    value=False, reason="physical load requires an itinerary"
                )
            }
        intensities = [
            activity.intensity.strip().lower()
            for day in ctx.output.days
            for activity in day.activities
        ]
        unknown = sorted(set(intensities) - _INTENSITY_LOAD.keys())
        if unknown:
            return {
                "known_intensity": EvaluationReason(
                    value=False, reason=f"unknown activity intensity: {unknown}"
                )
            }
        if not intensities:
            return {
                "known_intensity": EvaluationReason(
                    value=False, reason="itinerary has no activities"
                )
            }
        return {
            "known_intensity": EvaluationReason(value=True),
            "physical_load": sum(_INTENSITY_LOAD[intensity] for intensity in intensities),
        }


@dataclass
class LowFitnessSafety(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluationReason:
        if ctx.metadata is None:
            return EvaluationReason(value=False, reason="case is missing metadata")
        if ctx.metadata["fitness_level"] != FitnessLevel.LOW:
            return EvaluationReason(value=True)
        if not isinstance(ctx.output, ItineraryOut):
            return EvaluationReason(value=False, reason="low-fitness case is not an itinerary")
        unsafe = [
            activity.name
            for day in ctx.output.days
            for activity in day.activities
            if _is_unsafe_intensity(activity.intensity)
        ]
        return EvaluationReason(
            value=not unsafe,
            reason=None if not unsafe else f"unsafe low-fitness activities: {unsafe}",
        )


@dataclass
class PhysicalLoadComparisons(ReportEvaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    def evaluate(
        self,
        ctx: ReportEvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata],
    ) -> TableResult:
        loads: dict[tuple[int, FitnessLevel], list[float]] = {}
        for case in ctx.report.cases:
            if case.metadata is None or "physical_load" not in case.metrics:
                raise ValueError(f"{case.name} has no physical_load metric")
            key = (case.metadata["age"], case.metadata["fitness_level"])
            loads.setdefault(key, []).append(float(case.metrics["physical_load"]))
        means = {key: mean(values) for key, values in loads.items()}
        comparisons = [
            ("age 24: low <= high", means[(24, FitnessLevel.LOW)], means[(24, FitnessLevel.HIGH)]),
            ("age 78: low <= high", means[(78, FitnessLevel.LOW)], means[(78, FitnessLevel.HIGH)]),
            ("low fitness: age 78 <= 24", means[(78, FitnessLevel.LOW)], means[(24, FitnessLevel.LOW)]),
            ("high fitness: age 78 <= 24", means[(78, FitnessLevel.HIGH)], means[(24, FitnessLevel.HIGH)]),
        ]
        failed = [label for label, lower, upper in comparisons if lower > upper]
        if failed:
            raise ValueError(f"physical load comparisons failed: {failed}")
        return TableResult(
            title="Physical load comparisons",
            columns=["comparison", "left mean load", "right mean load", "passes"],
            rows=[[label, lower, upper, True] for label, lower, upper in comparisons],
        )


@dataclass
class FitnessAppropriateness(Evaluator[str, ItineraryOut | ClarificationOut, CaseMetadata]):
    async def evaluate(
        self, ctx: EvaluatorContext[str, ItineraryOut | ClarificationOut, CaseMetadata]
    ) -> EvaluatorOutput:
        return await build_fitness_appropriateness_judge().evaluate(ctx)


@lru_cache
def build_fitness_appropriateness_judge() -> LLMJudge:
    settings = get_settings()
    if settings.gemini_api_key is None:
        raise RuntimeError("GEMINI_API_KEY is required to run evals")
    judge_model = GoogleModel(
        GEMINI_JUDGE_MODEL,
        provider=GoogleProvider(api_key=settings.gemini_api_key.get_secret_value()),
    )
    return LLMJudge(
        rubric=(
            "The itinerary's activities (their intensity and description) are appropriate for "
            "the traveler's age and fitness level stated in the input prompt — for example, an "
            "elderly, low-fitness traveler must not be assigned strenuous, high-intensity "
            "activities, and a young, high-fitness traveler's itinerary should not be needlessly "
            "sedentary."
        ),
        include_input=True,
        model=judge_model,
    )
