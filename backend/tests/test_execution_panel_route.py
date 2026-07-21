"""Guards GET /api/trips/{id}/execution: honest-empty before any /plan run, real derived data
(tokens, cost, budget %) after one. Real DB rows, not values asserted straight off a mock.
"""

from app.config import (
    GEMINI_INPUT_PRICE_PER_MILLION_TOKENS,
    GEMINI_OUTPUT_PRICE_PER_MILLION_TOKENS,
    MAX_CONTEXT_TOKENS,
)
from tests.db_helpers import run_db, seed_agent_run, seed_execution_event, seed_trip


def test_execution_panel_before_any_plan_run_is_honestly_empty(client) -> None:
    trip_id = run_db(lambda session: seed_trip(session))

    response = client.get(f"/api/trips/{trip_id}/execution")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["agent_run"] is None, (
        f"a trip with no /plan run must report agent_run: null, never a fabricated run; "
        f"got {body['agent_run']}"
    )
    assert body["events"] == [], f"expected no events before any run, got {body['events']}"


def test_execution_panel_for_nonexistent_trip_is_404(client) -> None:
    response = client.get("/api/trips/999999/execution")

    assert response.status_code == 404, f"expected 404, got {response.status_code}: {response.text}"
    assert response.json()["code"] == "trip_not_found"


def test_execution_panel_reflects_a_real_agent_run_with_derived_cost_and_budget(client) -> None:
    trip_id = run_db(lambda session: seed_trip(session))
    run_db(
        lambda session: seed_agent_run(
            session, trip_id, total_input_tokens=10_000, total_output_tokens=2_000
        )
    )
    run_db(lambda session: seed_execution_event(session, trip_id))

    response = client.get(f"/api/trips/{trip_id}/execution")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["agent_run"]["total_input_tokens"] == 10_000
    assert body["agent_run"]["total_output_tokens"] == 2_000
    assert len(body["agent_run"]["steps"]) == 1, (
        f"expected the one seeded MODEL step, got {body['agent_run']['steps']}"
    )
    assert len(body["events"]) == 1, f"expected the one seeded event, got {body['events']}"

    expected_cost = round(
        (10_000 * GEMINI_INPUT_PRICE_PER_MILLION_TOKENS + 2_000 * GEMINI_OUTPUT_PRICE_PER_MILLION_TOKENS)
        / 1_000_000,
        6,
    )
    assert body["estimated_cost_usd"] == expected_cost, (
        f"cost must be derived from the real Gemini price table applied to the run's real "
        f"tokens, not a placeholder; got {body['estimated_cost_usd']}, expected {expected_cost}"
    )
    expected_budget_pct = round(100 * 12_000 / MAX_CONTEXT_TOKENS, 2)
    assert body["budget_utilization_pct"] == expected_budget_pct, (
        f"budget utilization must be (input+output tokens) / MAX_CONTEXT_TOKENS, got "
        f"{body['budget_utilization_pct']}, expected {expected_budget_pct}"
    )
