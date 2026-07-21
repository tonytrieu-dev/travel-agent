"""Eval runner entry point.

Run for real once the Gemini free-tier quota has reset:
    uv run python -m evals.run --repeat 3
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_evals.dataset import set_eval_attribute
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.activities_tavily import TavilyActivityProvider
from app.adapters.flights_searchapi import get_flight_provider
from app.agent.execution_log import execution_context
from app.agent.planner import PlannerDeps, agent, default_usage_limits
from app.agent.prompts import load_system_prompt
from app.config import GROQ_MODEL, get_settings
from app.db import get_session_factory
from app.models import TripRequest, User
from app.schemas import ClarificationOut, ItineraryOut
from evals.dataset import dataset
from evals.evaluators import WEB_SEARCH_URLS_ATTRIBUTE

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_web_search_urls(messages: list[ModelMessage]) -> list[str]:
    """Pull every URL the run's `web_search` tool returned, across all its calls, from the
    agent's real message history — see evaluators.py for why this stands in for span-tree
    inspection in this environment."""
    urls: list[str] = []
    for message in messages:
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or part.tool_name != "web_search":
                continue
            if not isinstance(part.content, list):
                continue
            for result in part.content:
                url = result.get("url") if isinstance(result, dict) else None
                if url:
                    urls.append(url)
    return urls


async def _open_eval_trip(session: AsyncSession) -> int:
    """`web_search`/`search_flights` route every call through `execution_log.record_event`,
    which hard-requires a bound `execution_context` (append-only audit log, enforced fail-loud —
    see `execution_log.py`) — the same invariant `dbos_runtime._run_planner_workflow` satisfies
    for a real `/plan` request. An eval case has no real trip behind it, so this creates one
    throwaway EVAL-tagged `TripRequest` per case-run purely to carry that foreign key; nothing
    about eval scoring reads it back."""
    user = User()
    session.add(user)
    await session.flush()
    assert user.id is not None
    trip = TripRequest(
        user_id=user.id,
        origin="EVAL",
        destination="EVAL",
        destination_airport="EVAL",
        depart_date="2026-01-01",
    )
    session.add(trip)
    await session.flush()
    assert trip.id is not None
    return trip.id


async def task(prompt: str) -> ItineraryOut | ClarificationOut:
    """The task pydantic_evals runs per case: a real agent.run against real dependencies."""
    settings = get_settings()
    deps = PlannerDeps(
        flight_provider=get_flight_provider(settings),
        activity_provider=TavilyActivityProvider(settings.tavily_api_key.get_secret_value()),
    )
    async with get_session_factory()() as session:
        trip_id = await _open_eval_trip(session)
        async with execution_context(session, trip_id):
            result = await agent.run(prompt, deps=deps, usage_limits=default_usage_limits())
    set_eval_attribute(WEB_SEARCH_URLS_ATTRIBUTE, _extract_web_search_urls(result.all_messages()))
    return result.output


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=_REPO_ROOT
    ).stdout.strip()


def _dataset_fingerprint() -> str:
    """A stable hash of the cases' defining content (name/inputs/metadata), so two runs against
    an unchanged dataset are directly comparable. Deliberately excludes wired-up evaluators —
    those are code, already covered by `git_sha`."""
    payload = [
        {"name": case.name, "inputs": case.inputs, "metadata": case.metadata}
        for case in dataset.cases
    ]
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_fingerprint() -> dict[str, str]:
    """Everything needed to tell whether two eval runs are comparable: same model, same system
    prompt, same dataset, and the exact commit the agent/eval code ran at."""
    return {
        "model": GROQ_MODEL,
        "system_prompt_sha256": hashlib.sha256(load_system_prompt().encode()).hexdigest(),
        "dataset_sha256": _dataset_fingerprint(),
        "git_sha": _git_sha(),
    }


def main(*, repeat: int = 1) -> None:
    fingerprint = build_fingerprint()
    print(f"Run fingerprint: {json.dumps(fingerprint, indent=2)}")
    report = dataset.evaluate_sync(task, repeat=repeat, metadata=fingerprint)
    report.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the travel-planner agent eval suite.")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Number of times to run each case (k-repeats)."
    )
    args = parser.parse_args()
    main(repeat=args.repeat)
