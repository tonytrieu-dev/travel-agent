"""Executable acceptance criteria for the HITL booking gate (features/booking_hitl.feature).

Each scenario drives the real /execute endpoint through the sync TestClient and asserts the
system's observable reaction plus the persisted audit state — never a value configured on a mock.
The booking-options provider is a counting spy so "books exactly once" is a real assertion about
quota calls, not about a fabricated return value.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.conftest import BookingOptionsFetchSpy
from tests.db_helpers import count_transitions_into, get_booking, run_db, seed_booking
from app.state import BookingState

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


@then("both execute responses return 200 with the same booking reference")
def _both_same_reference(bag: dict) -> None:
    responses = bag["responses"]
    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200], f"expected both execute calls to succeed, got {statuses}"
    references = {response.json()["booking_reference"] for response in responses}
    assert len(references) == 1 and None not in references, (
        f"idempotent execute must return one shared booking_reference, got {references}"
    )


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


@then("the booking ends EXECUTED with exactly one transition into EXECUTED")
def _one_executed_transition(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.EXECUTED, f"expected EXECUTED, got {booking.state}"
    executed_transitions = run_db(
        lambda session: count_transitions_into(session, log_id, BookingState.EXECUTED)
    )
    assert executed_transitions == 1, (
        f"exactly one audit transition into EXECUTED expected, got {executed_transitions}"
    )


@then(parsers.parse('the response is {status:d} with error code "{code}"'))
def _response_with_code(bag: dict, status: int, code: str) -> None:
    response = bag["response"]
    assert response.status_code == status, (
        f"expected {status} rejecting the request, got {response.status_code}: {response.text}"
    )
    actual_code = response.json()["code"]
    assert actual_code == code, f"expected error code {code!r}, got {actual_code!r}"


@then("no booking reference is stored on the booking")
def _no_reference(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.booking_reference is None, (
        f"a rejected execute must not write a booking_reference, found {booking.booking_reference!r}"
    )


@then("the booking is left EXPIRED with an audit transition into EXPIRED")
def _left_expired(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.EXPIRED, (
        f"a past-TTL execute must mark the booking EXPIRED, got {booking.state}"
    )
    expired_transitions = run_db(
        lambda session: count_transitions_into(session, log_id, BookingState.EXPIRED)
    )
    assert expired_transitions == 1, (
        f"exactly one audit transition into EXPIRED expected, got {expired_transitions}"
    )


@then("the booking is left CONFIRMED with no booking reference stored")
def _left_confirmed_no_reference(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.CONFIRMED, (
        f"an upstream booking-options failure must leave the booking retryable in CONFIRMED, "
        f"got {booking.state}"
    )
    assert booking.booking_reference is None, (
        f"a failed execute must not write a booking_reference, found {booking.booking_reference!r}"
    )
