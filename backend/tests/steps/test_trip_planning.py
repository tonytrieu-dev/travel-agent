"""Executable acceptance criteria for trip planning (features/trip_planning.feature).

Each scenario drives the real /api/trips* endpoints through the sync TestClient and asserts the
system's observable reaction plus the persisted state — never a value configured on a mock. The
flight-search and planner providers are counting spies so "reused the cache" / "never called
twice" are real assertions about calls made, not about a fabricated return value.
"""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.schemas import ClarificationOut, ItineraryDayOut, ItineraryOut
from tests.conftest import FlightSearchSpy, PlannerRunSpy
from tests.db_helpers import (
    get_trip,
    run_db,
    seed_flight_search_results,
    seed_itinerary,
    seed_trip,
)

scenarios("../../features/trip_planning.feature")


@pytest.fixture
def bag() -> dict:
    return {}


def _trip_payload(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "origin": "JFK",
        "destination": "Paris",
        "destination_airport": "CDG",
        "depart_date": "2026-08-01",
    }
    payload.update(overrides)
    return payload


@given("a trip request with a depart date next month and no return date")
def _valid_trip_request(bag: dict) -> None:
    bag["payload"] = _trip_payload(depart_date="2026-09-01")


@given("a trip request with a depart date in the past")
def _past_depart_date(bag: dict) -> None:
    bag["payload"] = _trip_payload(depart_date="2020-01-01")


@given("a trip request whose return date is before its depart date")
def _return_before_depart(bag: dict) -> None:
    bag["payload"] = _trip_payload(depart_date="2026-09-10", return_date="2026-09-01")


@when("the trip is created")
def _create_trip(client, bag: dict) -> None:
    bag["response"] = client.post("/api/trips", json=bag["payload"])


@then(parsers.parse('the response is {status:d} with status "{status_value}"'))
def _response_with_status(bag: dict, status: int, status_value: str) -> None:
    response = bag["response"]
    assert response.status_code == status, (
        f"expected {status}, got {response.status_code}: {response.text}"
    )
    assert response.json()["status"] == status_value, (
        f"expected status {status_value!r}, got {response.json().get('status')!r}"
    )


@then(parsers.parse('the response is {status:d} with error code "{code}"'))
def _response_with_error_code(bag: dict, status: int, code: str) -> None:
    response = bag["response"]
    assert response.status_code == status, (
        f"expected {status}, got {response.status_code}: {response.text}"
    )
    actual_code = response.json()["code"]
    assert actual_code == code, f"expected error code {code!r}, got {actual_code!r}"


@given("an existing trip", target_fixture="trip_id")
def _existing_trip() -> int:
    return run_db(lambda session: seed_trip(session, depart_date="2026-09-01"))


@given("an existing trip with no prior flight search", target_fixture="trip_id")
def _existing_trip_no_search() -> int:
    return run_db(lambda session: seed_trip(session, depart_date="2026-09-01"))


@given(
    "an existing trip that already has a generated itinerary", target_fixture="trip_id"
)
def _existing_trip_with_itinerary() -> int:
    async def _work(session):
        trip_id = await seed_trip(session, depart_date="2026-09-01")
        await seed_itinerary(session, trip_id)
        return trip_id

    return run_db(_work)


@when("the trip is updated with only a new budget")
def _update_budget(client, trip_id: int, bag: dict) -> None:
    bag["response"] = client.patch(f"/api/trips/{trip_id}", json={"budget_usd": 2500.0})


@when("the trip is updated with a return date before its existing depart date")
def _update_bad_return_date(client, trip_id: int, bag: dict) -> None:
    bag["response"] = client.patch(f"/api/trips/{trip_id}", json={"return_date": "2020-01-01"})


@when("a nonexistent trip is updated", target_fixture="trip_id")
def _update_nonexistent(client, bag: dict) -> int:
    bag["response"] = client.patch("/api/trips/999999", json={"budget_usd": 100.0})
    return 999999


@then("the response is 200 with the new budget and the original dates")
def _updated_budget_and_dates(bag: dict) -> None:
    response = bag["response"]
    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["budget_usd"] == 2500.0, f"expected the new budget to stick, got {body['budget_usd']}"
    assert body["depart_date"] == "2026-09-01", (
        f"a budget-only PATCH must not touch depart_date, got {body['depart_date']}"
    )


@then("the trip's stored return date is unchanged")
def _return_date_unchanged(trip_id: int) -> None:
    trip = run_db(lambda session: get_trip(session, trip_id))
    assert trip.return_date is None, (
        f"a rejected PATCH must not persist the invalid return_date, found {trip.return_date!r}"
    )


@given("flights have already been searched live for that route and those dates")
def _prior_live_search() -> None:
    async def _work(session):
        other_trip_id = await seed_trip(session, depart_date="2026-09-01")
        await seed_flight_search_results(session, other_trip_id, minutes_ago=5)

    run_db(_work)


@when("flights are searched for the trip")
def _search_flights(client, trip_id: int, bag: dict) -> None:
    bag["response"] = client.post(f"/api/trips/{trip_id}/flights/search")


@then(parsers.parse('the response is 200 with offers sourced "{source}"'))
def _offers_sourced(bag: dict, source: str) -> None:
    response = bag["response"]
    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    offers = response.json()["offers"]
    assert offers, "expected at least one offer in the response"
    assert all(offer["source"] == source for offer in offers), (
        f"expected every offer sourced {source!r}, got {[offer['source'] for offer in offers]}"
    )


@then("the flight provider is called exactly once")
def _flight_provider_called_once(flight_search_spy: FlightSearchSpy) -> None:
    assert flight_search_spy.calls == 1, (
        f"a cache-miss search must call the live provider exactly once, got "
        f"{flight_search_spy.calls}"
    )


@then("the flight provider is never called")
def _flight_provider_never_called(flight_search_spy: FlightSearchSpy) -> None:
    assert flight_search_spy.calls == 0, (
        f"a cache hit must skip the live provider entirely (the seeded prior search was "
        f"inserted directly into the DB, not through this spy); got {flight_search_spy.calls} "
        f"calls"
    )


@given("the planner will produce a ready itinerary")
def _planner_ready(planner_spy: PlannerRunSpy) -> None:
    planner_spy.output = ItineraryOut(
        days=[ItineraryDayOut(day_number=1, summary="Explore the city", activities=[])]
    )


@given("the planner will ask a clarifying question")
def _planner_clarify(planner_spy: PlannerRunSpy) -> None:
    planner_spy.output = ClarificationOut(questions=["What is your budget?"])


@when("the trip is planned")
def _plan_trip(client, trip_id: int, bag: dict) -> None:
    bag["response"] = client.post(f"/api/trips/{trip_id}/plan")


@then('the response is 200 with status "ready" and an itinerary')
def _plan_ready(bag: dict) -> None:
    response = bag["response"]
    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["status"] == "ready", f"expected status 'ready', got {body.get('status')!r}"
    assert body["itinerary"]["days"], "expected a non-empty itinerary"


@then("the trip's status becomes \"itinerary_ready\"")
def _trip_status_itinerary_ready(trip_id: int) -> None:
    trip = run_db(lambda session: get_trip(session, trip_id))
    assert trip.status == "itinerary_ready", (
        f"a ready plan must flip the trip to itinerary_ready, got {trip.status}"
    )


@then('the response is 200 with status "needs_clarification" and no itinerary stored')
def _plan_needs_clarification(bag: dict, trip_id: int) -> None:
    response = bag["response"]
    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["status"] == "needs_clarification", (
        f"expected status 'needs_clarification', got {body.get('status')!r}"
    )
    assert body["questions"], "expected at least one clarifying question"
    trip = run_db(lambda session: get_trip(session, trip_id))
    assert trip.status != "itinerary_ready", (
        f"a clarification response must not flip the trip to itinerary_ready, got {trip.status}"
    )


@then("the planner is never called")
def _planner_never_called(planner_spy: PlannerRunSpy) -> None:
    assert planner_spy.calls == 0, (
        f"an already-planned trip must reuse the stored itinerary, not re-run the agent; "
        f"got {planner_spy.calls} calls"
    )
