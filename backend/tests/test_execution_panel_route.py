"""Guards GET /api/trips/{id}/execution: honest-empty before any /plan run, real derived data
(tokens, cost, budget % per run) after one or more. Real DB rows, not values asserted straight
off a mock.
"""

from sqlalchemy import select

from app.config import (
    LLM_INPUT_PRICE_PER_MILLION_TOKENS,
    LLM_OUTPUT_PRICE_PER_MILLION_TOKENS,
    MAX_CONTEXT_TOKENS,
)
from app.models import AgentRun
from tests.db_helpers import (
    run_db,
    seed_agent_run,
    seed_execution_event,
    seed_flight_search_results,
    seed_trip,
)


def test_execution_panel_reflects_a_real_agent_run_with_derived_cost_and_budget(client) -> None:
    trip_id = run_db(lambda session: seed_trip(session))
    agent_run_id = run_db(
        lambda session: seed_agent_run(
            session, trip_id, total_input_tokens=10_000, total_output_tokens=2_000
        )
    )
    run_db(
        lambda session: seed_execution_event(
            session, trip_id, agent_run_id=agent_run_id
        )
    )

    response = client.get(f"/api/trips/{trip_id}/execution")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert len(body["agent_runs"]) == 1
    run = body["agent_runs"][0]
    assert run["total_input_tokens"] == 10_000
    assert run["total_output_tokens"] == 2_000
    assert len(run["steps"]) == 1
    assert len(run["events"]) == 1
    assert len(body["events"]) == 1

    expected_cost = round(
        (10_000 * LLM_INPUT_PRICE_PER_MILLION_TOKENS + 2_000 * LLM_OUTPUT_PRICE_PER_MILLION_TOKENS)
        / 1_000_000,
        6,
    )
    assert run["estimated_cost_usd"] == expected_cost, (
        f"cost must be derived from the real LLM price table applied to the run's real "
        f"tokens, not a placeholder; got {run['estimated_cost_usd']}, expected {expected_cost}"
    )
    expected_budget_pct = round(100 * 12_000 / MAX_CONTEXT_TOKENS, 2)
    assert run["budget_utilization_pct"] == expected_budget_pct, (
        f"budget utilization must be (input+output tokens) / MAX_CONTEXT_TOKENS, got "
        f"{run['budget_utilization_pct']}, expected {expected_budget_pct}"
    )


def test_execution_panel_lists_multiple_runs_newest_first(client) -> None:
    """A trip can accumulate more than one AgentRun (e.g. a clarification round trip re-plans).
    The panel must surface all of them, not just the latest, so a reviewer can see the run
    history — not just the current state."""
    trip_id = run_db(lambda session: seed_trip(session))
    first_run_id = run_db(lambda session: seed_agent_run(session, trip_id))
    second_run_id = run_db(lambda session: seed_agent_run(session, trip_id))

    response = client.get(f"/api/trips/{trip_id}/execution")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    run_ids = [run["id"] for run in response.json()["agent_runs"]]
    assert run_ids == [second_run_id, first_run_id]


def test_each_flight_search_records_a_run_even_when_the_second_reuses_cache(
    client, flight_search_spy
) -> None:
    trip_id = run_db(lambda session: seed_trip(session))

    assert client.post(f"/api/trips/{trip_id}/flights/search").status_code == 200
    assert client.post(f"/api/trips/{trip_id}/flights/search").status_code == 200

    response = client.get(f"/api/trips/{trip_id}/execution")
    assert response.status_code == 200
    runs = response.json()["agent_runs"]
    assert len(runs) == 2
    assert flight_search_spy.calls == 1
    assert all(
        run["model"] == "FlightSearchSpy"
        and run["total_input_tokens"] == 0
        and run["total_output_tokens"] == 0
        and run["estimated_cost_usd"] == 0
        and run["budget_utilization_pct"] == 0
        and len(run["steps"]) == 1
        and run["steps"][0]["kind"] == "tool"
        and run["steps"][0]["name"] == "search_flights"
        and len(run["events"]) == 1
        and run["events"][0]["name"] == "search_flights"
        for run in runs
    )


def test_cross_trip_cache_search_records_one_owned_run_without_calling_provider(
    client, flight_search_spy
) -> None:
    source_trip_id = run_db(lambda session: seed_trip(session))
    run_db(lambda session: seed_flight_search_results(session, source_trip_id))
    trip_id = run_db(lambda session: seed_trip(session))

    assert client.post(f"/api/trips/{trip_id}/flights/search").status_code == 200
    run = client.get(f"/api/trips/{trip_id}/execution").json()["agent_runs"][0]

    assert flight_search_spy.calls == 0
    assert len(run["steps"]) == 1
    assert len(run["events"]) == 1


def test_unavailable_flight_search_records_one_owned_run_and_tool_step(
    client, flight_search_spy
) -> None:
    trip_id = run_db(lambda session: seed_trip(session))
    flight_search_spy.offers = []
    flight_search_spy.unavailable_reason = "recorded upstream outage"

    response = client.post(f"/api/trips/{trip_id}/flights/search")
    run = client.get(f"/api/trips/{trip_id}/execution").json()["agent_runs"][0]

    assert response.status_code == 200
    assert response.json()["unavailable_reason"] == "recorded upstream outage"
    assert len(run["steps"]) == 1
    assert run["steps"][0]["status"] == "unavailable"
    assert len(run["events"]) == 1
    assert run["events"][0]["status"] == "unavailable"


def test_searching_flights_for_a_nonexistent_trip_returns_404_without_creating_a_run(
    client,
) -> None:
    response = client.post("/api/trips/999999/flights/search")

    async def _agent_runs(session):
        return list(await session.scalars(select(AgentRun)))

    assert response.status_code == 404
    assert response.json()["code"] == "trip_not_found"
    assert run_db(_agent_runs) == []


def test_execution_panel_before_any_plan_run_is_honestly_empty(client) -> None:
    """A trip that has never been planned must report no runs and no events, never a fabricated
    placeholder run — the panel is only ever real persisted data."""
    trip_id = run_db(lambda session: seed_trip(session))

    response = client.get(f"/api/trips/{trip_id}/execution")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["agent_runs"] == [], (
        f"a trip with no /plan run must report agent_runs: [], never a fabricated run; "
        f"got {body['agent_runs']}"
    )
    assert body["events"] == []


def test_execution_panel_for_nonexistent_trip_is_404(client) -> None:
    response = client.get("/api/trips/999999/execution")

    assert response.status_code == 404, f"expected 404, got {response.status_code}: {response.text}"
    assert response.json()["code"] == "trip_not_found"
