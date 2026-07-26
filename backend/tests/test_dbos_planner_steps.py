"""Guards the DBOS replay gap: a crash mid-run must not silently re-issue an already-completed
Cerebras call, search_flights call, or web_search call. Each external-call boundary the durable
workflow drives must be wrapped in @DBOS.step, so replay reuses the recorded result instead of
paying for the same call twice. These check the wrapping is actually applied at each call site —
DBOS's own step-memoization mechanics (record-once-per-workflow-id) are the library's tested
guarantee, not something re-proven here."""

from dataclasses import dataclass, field

from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.flights_searchapi import FlightSearchOutcome
from app.agent.planner import agent as planner_agent
from app.dbos_runtime import agent as durable_agent
from app.dbos_runtime import run_planner_durable
from tests.db_helpers import TEST_DATABASE_URL, seed_trip


def _dbos_step_name(bound_method: object) -> str | None:
    return getattr(bound_method, "dbos_function_name", None)


async def _seed_trip_id() -> int:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            trip_id = await seed_trip(session)
            await session.commit()
            return trip_id
    finally:
        await engine.dispose()


def test_cerebras_request_is_wrapped_as_a_dbos_step() -> None:
    assert isinstance(durable_agent.model, Model), "agent must be built with a real Model instance"
    assert _dbos_step_name(durable_agent.model.request) == "cerebras_request", (
        "agent.model.request must be wrapped in @DBOS.step(name='cerebras_request') so a "
        "workflow replay reuses the recorded completion instead of re-billing an identical "
        "Cerebras call"
    )


@dataclass
class _FlightProviderSpy:
    calls: int = 0
    offers: list = field(default_factory=list)

    async def search_offers(
        self, departure_id: str, arrival_id: str, outbound_date: str, return_date: str | None
    ) -> FlightSearchOutcome:
        self.calls += 1
        return FlightSearchOutcome(offers=self.offers, unavailable_reason=None)

    async def fetch_booking_options(self, *args: object, **kwargs: object) -> list[dict]:
        raise NotImplementedError


async def test_run_planner_durable_wraps_flight_search_as_a_dbos_step(client, monkeypatch) -> None:
    """The flight provider is constructed fresh per run inside _run_planner_workflow (not the
    shared agent singleton), so the wrap has to be asserted on the actual instance the workflow
    builds, not a module-level object."""
    trip_id = await _seed_trip_id()
    captured: list[_FlightProviderSpy] = []

    def _fake_get_flight_provider(_settings: object) -> _FlightProviderSpy:
        spy = _FlightProviderSpy()
        captured.append(spy)
        return spy

    monkeypatch.setattr("app.dbos_runtime.get_flight_provider", _fake_get_flight_provider)

    with planner_agent.override(model=TestModel(custom_output_args={"days": []})):
        await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    assert captured, "get_flight_provider must be called once by the durable workflow"
    assert _dbos_step_name(captured[0].search_offers) == "search_flights_offers", (
        "the flight provider's search_offers must be wrapped in @DBOS.step so a workflow replay "
        "reuses the recorded offers instead of re-calling the live flight search API"
    )


async def test_run_planner_durable_wraps_activity_search_as_a_dbos_step(
    client, monkeypatch
) -> None:
    trip_id = await _seed_trip_id()

    class _ActivityProviderSpy:
        def __init__(self, api_key: str) -> None:
            captured.append(self)

        async def search(self, query: str, max_results: int = 5) -> list:
            return []

    captured: list[_ActivityProviderSpy] = []
    monkeypatch.setattr("app.dbos_runtime.TavilyActivityProvider", _ActivityProviderSpy)
    monkeypatch.setattr("app.dbos_runtime.get_flight_provider", lambda _settings: _FlightProviderSpy())

    with planner_agent.override(model=TestModel(custom_output_args={"days": []})):
        await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    assert captured, "TavilyActivityProvider must be constructed once by the durable workflow"
    assert _dbos_step_name(captured[0].search) == "web_search", (
        "the activity provider's search must be wrapped in @DBOS.step so a workflow replay "
        "reuses the recorded results instead of re-calling Tavily"
    )
