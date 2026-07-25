"""Guards the planner agent's web_search tool against grounding activities in the wrong
same-named place (e.g. Ontario, Canada vs. Ontario, CA/ONT) — see planner.py's web_search for
the full reasoning.
"""

from dataclasses import dataclass, field

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from app.adapters.activities_tavily import NormalizedActivityResult
from app.agent.execution_log import execution_context
from app.agent.planner import PlannerDeps, web_search
from tests.db_helpers import run_db, seed_trip


@dataclass
class ActivitySearchSpy:
    """Stands in for the real Tavily ``ActivityProvider`` and records the query it was actually
    called with, so a test can assert on what reached the provider, not just the model's args."""

    results: list[NormalizedActivityResult] = field(default_factory=list)
    last_query: str | None = None

    async def search(self, query: str, max_results: int = 5) -> list[NormalizedActivityResult]:
        self.last_query = query
        return self.results


def _context(activity_provider: ActivitySearchSpy) -> RunContext[PlannerDeps]:
    deps = PlannerDeps(flight_provider=None, activity_provider=activity_provider)  # type: ignore[arg-type]
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def test_web_search_anchors_the_query_on_the_trips_airport_code() -> None:
    """A bare destination name can collide with a same-named place elsewhere; the airport code
    the traveler picked is unambiguous, so it must reach the provider even when the model's own
    query text doesn't include it."""

    async def _work(session):
        trip_id = await seed_trip(session, destination_airport="ONT")
        provider = ActivitySearchSpy()
        async with execution_context(session, trip_id):
            await web_search(_context(provider), "things to do in Ontario")
        return provider

    provider = run_db(_work)

    assert provider.last_query is not None and "ONT" in provider.last_query, (
        f"web_search must anchor the model's query on the trip's destination_airport so results "
        f"can't drift to a same-named place elsewhere; got {provider.last_query!r}"
    )
