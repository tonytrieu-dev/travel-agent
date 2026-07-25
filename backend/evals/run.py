import argparse
import asyncio
import re
import time
from functools import partial
from typing import Literal

from pydantic_evals import Dataset
from pydantic_evals.dataset import set_eval_attribute
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.activities_tavily import (
    RecordedActivityProvider,
    TavilyActivityProvider,
)
from app.adapters.flights_searchapi import LiveSearchApiProvider, RecordedProvider
from app.agent.execution_log import execution_context
from app.agent.planner import PlannerDeps, agent, default_usage_limits
from app.config import (
    ACTIVITY_CASSETTE_PATH,
    CEREBRAS_MODEL,
    FLIGHT_CASSETTE_DIR,
    GEMINI_JUDGE_MODEL,
    get_settings,
)
from app.db import get_session_factory
from app.models import FitnessLevel, TripRequest, User
from app.schemas import ClarificationOut, ItineraryOut
from evals.dataset import dataset
from evals.evaluators import (
    PLANNER_TRACE_ATTRIBUTE,
    CaseMetadata,
    FitnessAppropriateness,
    extract_planner_trace,
)

ProviderMode = Literal["recorded", "live-smoke"]


async def _open_eval_trip(session: AsyncSession) -> int:
    user = User()
    session.add(user)
    await session.flush()
    assert user.id is not None
    trip = TripRequest(
        user_id=user.id,
        origin="JFK",
        destination="San Diego",
        destination_airport="SAN",
        depart_date="2026-09-01",
        return_date="2026-09-08",
    )
    session.add(trip)
    await session.flush()
    assert trip.id is not None
    return trip.id


def _fitness_level_from_prompt(prompt: str) -> FitnessLevel | None:
    match = re.search(r"Fitness level: (\w+)\.", prompt)
    if match is None:
        return None
    try:
        return FitnessLevel(match.group(1).lower())
    except ValueError:
        return None


def _planner_deps(prompt: str, provider_mode: ProviderMode) -> PlannerDeps:
    if provider_mode == "recorded":
        return PlannerDeps(
            flight_provider=RecordedProvider(FLIGHT_CASSETTE_DIR),
            activity_provider=RecordedActivityProvider(ACTIVITY_CASSETTE_PATH),
            fitness_level=_fitness_level_from_prompt(prompt),
        )
    settings = get_settings()
    return PlannerDeps(
        flight_provider=LiveSearchApiProvider(settings.searchapi_api_key.get_secret_value()),
        activity_provider=TavilyActivityProvider(settings.tavily_api_key.get_secret_value()),
        fitness_level=_fitness_level_from_prompt(prompt),
    )


# Cerebras meters gpt-oss-120b at 30_000 tokens/minute — the whole budget one case may spend.
CEREBRAS_TOKEN_WINDOW_SECONDS = 60.0

_previous_case_finished_at: float | None = None


async def _wait_out_previous_case_token_window() -> None:
    if _previous_case_finished_at is None:
        return
    seconds_remaining = CEREBRAS_TOKEN_WINDOW_SECONDS - (
        time.monotonic() - _previous_case_finished_at
    )
    if seconds_remaining > 0:
        await asyncio.sleep(seconds_remaining)


async def task(
    prompt: str, *, provider_mode: ProviderMode = "recorded"
) -> ItineraryOut | ClarificationOut:
    global _previous_case_finished_at
    await _wait_out_previous_case_token_window()
    deps = _planner_deps(prompt, provider_mode)
    try:
        async with get_session_factory()() as session:
            trip_id = await _open_eval_trip(session)
            async with execution_context(session, trip_id):
                result = await agent.run(prompt, deps=deps, usage_limits=default_usage_limits())
    finally:
        _previous_case_finished_at = time.monotonic()
    set_eval_attribute(PLANNER_TRACE_ATTRIBUTE, extract_planner_trace(result.all_messages()))
    return result.output


def build_run_metadata(provider_mode: ProviderMode = "recorded") -> dict[str, str]:
    return {
        "model": CEREBRAS_MODEL,
        "judge_model": GEMINI_JUDGE_MODEL,
        "provider_mode": provider_mode,
    }


def _selected_dataset(
    provider_mode: ProviderMode, *, with_judge: bool
) -> Dataset[str, ItineraryOut | ClarificationOut, CaseMetadata]:
    evaluators = [*dataset.evaluators, FitnessAppropriateness()] if with_judge else dataset.evaluators
    if provider_mode == "live-smoke":
        return Dataset(
            name=f"{dataset.name}_live_smoke",
            cases=dataset.cases[:1],
            evaluators=evaluators,
        )
    return Dataset(
        name=dataset.name,
        cases=dataset.cases,
        evaluators=evaluators,
        report_evaluators=dataset.report_evaluators,
    )


def main(*, repeat: int = 1, live_smoke: bool = False, with_judge: bool = False) -> None:
    provider_mode: ProviderMode = "live-smoke" if live_smoke else "recorded"
    metadata = build_run_metadata(provider_mode)
    print(f"Run metadata: {metadata}")
    selected_dataset = _selected_dataset(provider_mode, with_judge=with_judge)
    report = selected_dataset.evaluate_sync(
        partial(task, provider_mode=provider_mode),
        repeat=1 if live_smoke else repeat,
        metadata=metadata,
        max_concurrency=1,
    )
    report.print(include_reasons=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the travel-planner agent eval suite.")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Number of times to run each case (k-repeats)."
    )
    parser.add_argument("--live-smoke", action="store_true")
    parser.add_argument(
        "--with-judge",
        action="store_true",
        help="Include the Gemini FitnessAppropriateness LLMJudge (requires GEMINI_API_KEY).",
    )
    args = parser.parse_args()
    main(repeat=args.repeat, live_smoke=args.live_smoke, with_judge=args.with_judge)
