"""Executable acceptance criteria for the HITL booking gate (features/booking_hitl.feature).

Each scenario drives the real /execute endpoint through the sync TestClient and asserts the
system's observable reaction plus the persisted audit state — never a value configured on a mock.
The booking-options provider is a counting spy so "books exactly once" is a real assertion about
quota calls, not about a fabricated return value.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.state import BookingState
from tests.conftest import BookingOptionsFetchSpy
from tests.db_helpers import count_transitions_into, get_booking, run_db, seed_booking

scenarios("../../features/booking_hitl.feature")


@pytest.fixture
def bag() -> dict:
    return {}


@given("a confirmed booking whose price hold is still valid", target_fixture="log_id")
def _confirmed_valid() -> int:
    return run_db(
        lambda session: seed_booking(
            session, state=BookingState.CONFIRMED, expires_in_minutes=30
        )
    )


@given("a booking still pending user confirmation", target_fixture="log_id")
def _pending() -> int:
    return run_db(
        lambda session: seed_booking(
            session, state=BookingState.PENDING_USER_CONFIRMATION, expires_in_minutes=30
        )
    )


@given("a confirmed booking whose price hold has already expired", target_fixture="log_id")
def _confirmed_expired() -> int:
    return run_db(
        lambda session: seed_booking(
            session, state=BookingState.CONFIRMED, expires_in_minutes=-1
        )
    )


@given("the booking-options provider will fail")
def _provider_will_fail(booking_options_spy: BookingOptionsFetchSpy) -> None:
    booking_options_spy.should_fail = True


@when("execute is called twice concurrently")
def _execute_twice(client, log_id: int, bag: dict) -> None:
    url = f"/api/bookings/{log_id}/execute"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, url), pool.submit(client.post, url)]
        bag["responses"] = [future.result() for future in futures]


@when("execute is called once")
def _execute_once(client, log_id: int, bag: dict) -> None:
    bag["response"] = client.post(f"/api/bookings/{log_id}/execute")


@when("the booking is cancelled and the same flight is requested again")
def _cancel_and_request_again(client, log_id: int, bag: dict) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert client.post(f"/api/bookings/{log_id}/cancel").status_code == 200
    bag["response"] = client.post(
        f"/api/trips/{booking.trip_request_id}/booking/request",
        json={"flight_search_result_id": booking.flight_search_result_id},
    )
    bag["original_log_id"] = log_id


@then("a new pending booking is returned for the same flight")
def _new_pending_booking(bag: dict) -> None:
    response = bag["response"]
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] != bag["original_log_id"]
    assert body["state"] == BookingState.PENDING_USER_CONFIRMATION


@then("both execute responses return 200 with the same booking reference")
def _both_same_reference(bag: dict) -> None:
    responses = bag["responses"]
    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200]
    references = {response.json()["booking_reference"] for response in responses}
    assert len(references) == 1 and None not in references


@then("the booking-options provider is called exactly once")
def _provider_called_once(booking_options_spy: BookingOptionsFetchSpy) -> None:
    assert booking_options_spy.calls == 1, (
        f"the FOR UPDATE claim must fetch booking options exactly once; "
        f"got {booking_options_spy.calls} (a second fetch means a double quota burn)"
    )


@then("the booking-options provider is never called")
def _provider_never_called(booking_options_spy: BookingOptionsFetchSpy) -> None:
    assert booking_options_spy.calls == 0, (
        f"a rejected execute must not fetch booking options; got {booking_options_spy.calls}"
    )


@then("the booking-options provider receives the flight's route and outbound date")
def _provider_receives_route_and_date(booking_options_spy: BookingOptionsFetchSpy) -> None:
    """Regression guard: SearchApi's booking-options engine 400s "Missing required parameter
    departure_id" when the DBOS step forwards only booking_token — these must be pulled from
    the booked flight's raw_offer, not dropped on the way from _fetch_booking_options_step."""
    assert booking_options_spy.last_call_params == {
        "booking_token": "tok-abc",
        "departure_id": "JFK",
        "arrival_id": "CDG",
        "outbound_date": "2026-08-01",
        "return_date": None,
    }, (
        f"expected departure_id/arrival_id/outbound_date derived from the seeded flight's "
        f"raw_offer, and return_date from the seeded (one-way) trip, got "
        f"{booking_options_spy.last_call_params}"
    )


@then("the booking ends EXECUTED with exactly one transition into EXECUTED")
def _one_executed_transition(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.EXECUTED
    executed_transitions = run_db(
        lambda session: count_transitions_into(session, log_id, BookingState.EXECUTED)
    )
    assert executed_transitions == 1


@then(parsers.parse('the response is {status:d} with error code "{code}"'))
def _response_with_code(bag: dict, status: int, code: str) -> None:
    response = bag["response"]
    assert response.status_code == status, (
        f"expected {status} rejecting the request, got {response.status_code}: {response.text}"
    )
    assert response.json()["code"] == code


@then("no booking reference is stored on the booking")
def _no_reference(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.booking_reference is None, (
        f"a rejected execute must not write a booking_reference, found {booking.booking_reference!r}"
    )


@then("the booking is left EXPIRED with an audit transition into EXPIRED")
def _left_expired(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.EXPIRED
    expired_transitions = run_db(
        lambda session: count_transitions_into(session, log_id, BookingState.EXPIRED)
    )
    assert expired_transitions == 1


@then("the response is 200 with a booking reference and no booking options stored")
def _response_with_reference_no_options(bag: dict) -> None:
    response = bag["response"]
    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["booking_reference"], (
        "an upstream booking-options failure must not block the already-real, human-confirmed "
        f"execute from completing with its own reference, got {body}"
    )
    assert not body["booking_options"], (
        f"a failed booking-options fetch must degrade to empty, never a fabricated link, got "
        f"{body['booking_options']!r}"
    )
