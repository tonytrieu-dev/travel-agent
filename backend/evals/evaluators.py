"""Deterministic evaluators for the travel-planner eval suite, plus the LLMJudge factory.

`ctx.span_tree` (pydantic_evals' OTel-based tool-call inspection) is not usable in this
environment: only `opentelemetry-api` is installed, not `opentelemetry-sdk`, so
`pydantic_evals.otel._context_subtree.context_subtree()` unconditionally yields a
`SpanTreeRecordingError` (verified directly against the installed package — no real
TracerProvider is ever configured, so `add_span_processor` is never available). Citation
grounding instead reads the run's tool-call history off `ctx.attributes`, populated by the
task function calling `pydantic_evals.dataset.set_eval_attribute(WEB_SEARCH_URLS_ATTRIBUTE, ...)`
after the agent run completes — the message-history equivalent of the span-tree approach, and
the mechanism `EvaluatorContext.attributes` itself documents for exactly this purpose.
"""

from dataclasses import dataclass
from typing import Literal, TypedDict

from pydantic_ai.models.cerebras import CerebrasModel
from pydantic_ai.providers.cerebras import CerebrasProvider
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext, LLMJudge

from app.config import CEREBRAS_MODEL, get_settings
from app.schemas import ClarificationOut, ItineraryOut

WEB_SEARCH_URLS_ATTRIBUTE = "web_search_urls"
"""Key the run's task function stores the run's web_search result URLs under via
`set_eval_attribute`, and `CitationGrounding` reads them back from `ctx.attributes`."""


class CaseMetadata(TypedDict):
    """Per-case expectation: which member of the agent's `output_type` union this prompt should
    resolve to. `ClarificationOut` is still a valid resolution for a genuinely ambiguous input
    (age/fitness/dates are mandatory at trip intake now, so they're never the trigger)."""

    expects: Literal["clarification", "itinerary"]


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

        returned_urls = set(ctx.attributes.get(WEB_SEARCH_URLS_ATTRIBUTE, []))
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


def build_fitness_appropriateness_judge() -> LLMJudge:
    """An LLMJudge scoring itinerary/fitness fit, on the same model the agent runs on."""
    settings = get_settings()
    judge_model = CerebrasModel(
        CEREBRAS_MODEL,
        provider=CerebrasProvider(api_key=settings.cerebras_api_key.get_secret_value()),
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
