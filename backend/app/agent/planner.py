"""The travel-planner agent: union output (asks or plans), two composable read-only tools."""

import re
import time
from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.cerebras import CerebrasModel
from pydantic_ai.providers.cerebras import CerebrasProvider
from pydantic_ai.usage import UsageLimits

from app.adapters.activities_tavily import ActivityProvider
from app.adapters.flights_searchapi import FlightProvider
from app.agent.execution_log import current_trip, record_event
from app.agent.prompts import load_system_prompt, sanitize_web_content
from app.config import (
    CEREBRAS_MODEL,
    MAX_CONTEXT_TOKENS,
    MAX_OUTPUT_RETRIES,
    MAX_REQUESTS_PER_RUN,
    MAX_TOOL_STEPS,
    MAX_WEB_SEARCH_RESULTS,
    get_settings,
)
from app.models import ExecutionEventKind, FitnessLevel, TripRequest
from app.repositories.trips_repository import (
    flight_provider_name,
    get_recent_flight_results,
    offer_summary,
)
from app.schemas import ClarificationOut, ItineraryOut

_IATA_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


@dataclass
class PlannerDeps:
    flight_provider: FlightProvider
    activity_provider: ActivityProvider
    fitness_level: FitnessLevel | None = None


def _activity_provider_name(provider: ActivityProvider) -> str:
    return {
        "TavilyActivityProvider": "Tavily",
        "RecordedActivityProvider": "Recorded activities",
    }.get(type(provider).__name__, type(provider).__name__)


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

    # Route/dates never change mid-plan — reuse an existing search instead of repeating it.
    session, trip_id = current_trip("search_flights")
    cached_offers = await get_recent_flight_results(session, trip_id)
    if cached_offers:
        await record_event(
            ExecutionEventKind.API_CALL,
            "search_flights",
            "ok",
            f"{len(cached_offers)} offers (reused from this trip's earlier search)",
            data={"offers": [offer_summary(offer) for offer in cached_offers]},
            provider=flight_provider_name(ctx.deps.flight_provider),
        )
        return {
            "offers": [offer_summary(offer) for offer in cached_offers],
            "unavailable_reason": None,
            "source": "cached",
        }

    # Trust boundary: on a cache miss, search the STORED trip's own route/dates — never the
    # model-supplied tool arguments. Those arguments are validated for shape above, but a model
    # that passes a different (well-formed) departure_id/arrival_id must not be able to redirect
    # the search away from the trip the user actually created.
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise ModelRetry(f"trip {trip_id} is gone; cannot search flights for it")
    started_at = time.monotonic()
    outcome = await ctx.deps.flight_provider.search_offers(
        trip.origin, trip.destination_airport, trip.depart_date, trip.return_date
    )
    duration_ms = round((time.monotonic() - started_at) * 1000)
    await record_event(
        ExecutionEventKind.API_CALL,
        "search_flights",
        "ok" if outcome.unavailable_reason is None else "unavailable",
        f"{len(outcome.offers)} offers" if outcome.unavailable_reason is None
        else outcome.unavailable_reason,
        duration_ms,
        data={"offers": [offer_summary(offer) for offer in outcome.offers]},
        provider=flight_provider_name(ctx.deps.flight_provider),
    )
    return {
        "offers": [offer_summary(offer) for offer in outcome.offers],
        "unavailable_reason": outcome.unavailable_reason,
        "source": "live",
    }


async def web_search(
    ctx: RunContext[PlannerDeps], query: str, max_results: int = MAX_WEB_SEARCH_RESULTS
) -> list[dict]:
    """Research real, source-attributed activities or information."""
    started_at = time.monotonic()
    # Clamp both ends: the ceiling protects the provider token budget, and the floor of 1 keeps a
    # model-supplied 0 or negative from reaching Tavily (which would waste the call on no results).
    clamped_max_results = max(1, min(max_results, MAX_WEB_SEARCH_RESULTS))
    results = await ctx.deps.activity_provider.search(query, max_results=clamped_max_results)
    duration_ms = round((time.monotonic() - started_at) * 1000)
    await record_event(
        ExecutionEventKind.API_CALL,
        "web_search",
        "ok",
        f"{len(results)} results for query={query!r}",
        duration_ms,
        data={"results": [{"title": result.title, "url": result.url} for result in results]},
        provider=_activity_provider_name(ctx.deps.activity_provider),
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
    model = CerebrasModel(
        CEREBRAS_MODEL,
        provider=CerebrasProvider(api_key=settings.cerebras_api_key.get_secret_value()),
    )
    built_agent = Agent(
        model,
        deps_type=PlannerDeps,
        output_type=[ItineraryOut, ClarificationOut],
        system_prompt=load_system_prompt(),
        retries={"output": MAX_OUTPUT_RETRIES},
    )
    built_agent.instrument = True
    built_agent.tool(strict=True)(search_flights)
    built_agent.tool(strict=True)(web_search)
    return built_agent


agent = _build_agent()


def _web_search_urls(messages: list[ModelMessage]) -> set[str]:
    urls: set[str] = set()
    for message in messages:
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or part.tool_name != "web_search":
                continue
            if isinstance(part.content, list):
                urls.update(
                    result["url"]
                    for result in part.content
                    if isinstance(result, dict) and result.get("url")
                )
    return urls


@agent.output_validator
def reject_ungrounded_itinerary(
    ctx: RunContext[PlannerDeps], output: ItineraryOut | ClarificationOut
) -> ItineraryOut | ClarificationOut:
    """Every activity's source_url must be a URL web_search actually returned this run — the
    structural enforcement of "never fabricate an activity" (the prompt alone doesn't hold)."""
    if not isinstance(output, ItineraryOut):
        return output
    grounded = _web_search_urls(ctx.messages)
    ungrounded = [
        activity.source_url
        for day in output.days
        for activity in day.activities
        if activity.source_url not in grounded
    ]
    if ungrounded:
        raise ModelRetry(
            f"{len(ungrounded)} activity source_url(s) were not returned by web_search: "
            f"{ungrounded}. Call web_search to research real activities for this destination, then "
            "set every activity's source_url to a URL web_search actually returned. Never invent "
            "an activity or a URL, and never use a flight search as an activity."
        )
    return output


# A low-fitness traveler must not be handed a strenuous activity. The model labels intensity in
# free text, so match on normalized substrings — casing ("High"), phrasing ("very high"), and
# synonyms ("strenuous") all describe the same unsafe level and must all be caught.
_UNSAFE_INTENSITY_TERMS = ("high", "strenuous", "extreme", "vigorous", "intense")


def _is_unsafe_intensity(intensity: str) -> bool:
    normalized = intensity.strip().lower()
    return any(term in normalized for term in _UNSAFE_INTENSITY_TERMS)


@agent.output_validator
def reject_unsafe_intensity(
    ctx: RunContext[PlannerDeps], output: ItineraryOut | ClarificationOut
) -> ItineraryOut | ClarificationOut:
    """Structural enforcement of "match intensity to fitness" — the prompt alone doesn't hold,
    same reasoning as reject_ungrounded_itinerary above."""
    if not isinstance(output, ItineraryOut) or ctx.deps.fitness_level != FitnessLevel.LOW:
        return output
    unsafe = [
        activity.name
        for day in output.days
        for activity in day.activities
        if _is_unsafe_intensity(activity.intensity)
    ]
    if unsafe:
        raise ModelRetry(
            f"Traveler fitness level is {ctx.deps.fitness_level.value}, but these activities are "
            f"too high intensity: {unsafe}. Replace them with gentler, shorter-distance options."
        )
    return output
