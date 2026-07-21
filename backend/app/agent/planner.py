"""The travel-planner agent: union output (asks or plans), two composable read-only tools."""

import re
import time
from dataclasses import asdict, dataclass

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.usage import UsageLimits

from app.adapters.activities_tavily import TavilyActivityProvider
from app.adapters.flights_searchapi import FlightProvider
from app.agent.execution_log import record_event
from app.agent.prompts import load_system_prompt, sanitize_web_content
from app.agent.tool_gate import ToolClassification, register_tool
from app.config import (
    GROQ_MODEL,
    MAX_CONTEXT_TOKENS,
    MAX_REQUESTS_PER_RUN,
    MAX_TOOL_STEPS,
    get_settings,
)
from app.models import ExecutionEventKind
from app.schemas import ClarificationOut, ItineraryOut

_IATA_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


@dataclass
class PlannerDeps:
    flight_provider: FlightProvider
    activity_provider: TavilyActivityProvider


def default_usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=MAX_REQUESTS_PER_RUN,
        tool_calls_limit=MAX_TOOL_STEPS,
        total_tokens_limit=MAX_CONTEXT_TOKENS,
    )


async def search_flights(
    ctx: RunContext[PlannerDeps],
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: str | None = None,
) -> dict:
    """Search real Google Flights offers between two IATA airport codes."""
    if not _IATA_CODE_PATTERN.match(departure_id) or not _IATA_CODE_PATTERN.match(arrival_id):
        raise ModelRetry(
            f"departure_id and arrival_id must be 3-letter IATA codes (e.g. JFK), got "
            f"departure_id={departure_id!r} arrival_id={arrival_id!r}"
        )
    started_at = time.monotonic()
    outcome = await ctx.deps.flight_provider.search_offers(
        departure_id, arrival_id, outbound_date, return_date
    )
    duration_ms = round((time.monotonic() - started_at) * 1000)
    await record_event(
        ExecutionEventKind.API_CALL,
        "search_flights",
        "ok" if outcome.unavailable_reason is None else "unavailable",
        f"{len(outcome.offers)} offers" if outcome.unavailable_reason is None
        else outcome.unavailable_reason,
        duration_ms,
    )
    return {
        "offers": [asdict(offer) for offer in outcome.offers],
        "unavailable_reason": outcome.unavailable_reason,
    }


async def web_search(ctx: RunContext[PlannerDeps], query: str, max_results: int = 5) -> list[dict]:
    """Research real, source-attributed activities or information."""
    started_at = time.monotonic()
    results = await ctx.deps.activity_provider.search(query, max_results=max_results)
    duration_ms = round((time.monotonic() - started_at) * 1000)
    await record_event(
        ExecutionEventKind.API_CALL,
        "web_search",
        "ok",
        f"{len(results)} results for query={query!r}",
        duration_ms,
    )
    return [
        {
            "title": result.title,
            "url": result.url,
            "content": sanitize_web_content(result.content),
            "score": result.score,
        }
        for result in results
    ]


def _build_agent() -> Agent[PlannerDeps, ItineraryOut | ClarificationOut]:
    settings = get_settings()
    model = GroqModel(
        GROQ_MODEL, provider=GroqProvider(api_key=settings.groq_api_key.get_secret_value())
    )
    built_agent = Agent(
        model,
        deps_type=PlannerDeps,
        output_type=[ItineraryOut, ClarificationOut],
        system_prompt=load_system_prompt(),
    )
    built_agent.instrument = True
    register_tool(built_agent, search_flights, classification=ToolClassification.READ_ONLY)
    register_tool(built_agent, web_search, classification=ToolClassification.READ_ONLY)
    return built_agent


agent = _build_agent()
