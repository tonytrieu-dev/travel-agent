"""Guards GET /api/bookings, which backs the global approval-history tab: every booking the
authenticated user requested, each carrying its append-only BookingTransition trail. Real DB rows
via the real routes, so the transitions come from the state machine rather than a fixture.
"""

from app.config import DEMO_USER_EMAIL
from app.state import BookingState
from tests.db_helpers import run_db, seed_booking, seed_flight_search_results


def _create_trip_payload() -> dict:
    return {
        "origin": "JFK",
        "destination": "Paris",
        "destination_airport": "CDG",
        "depart_date": "2099-08-01",
        "return_date": "2099-08-08",
        "age": 30,
        "fitness_level": "moderate",
    }


def _request_booking(client) -> int:
    """A trip created through the route is owned by the authenticated demo user, so the booking it
    produces is one the approval-history endpoint should return."""
    trip_id = client.post("/api/trips", json=_create_trip_payload()).json()["id"]
    flight_ids = run_db(lambda session: seed_flight_search_results(session, trip_id))
    response = client.post(
        f"/api/trips/{trip_id}/booking/request", json={"flight_search_result_id": flight_ids[0]}
    )
    assert response.status_code == 200, f"booking request failed: {response.text}"
    return response.json()["id"]


def test_approval_history_returns_each_owned_bookings_transition_trail_newest_first(client) -> None:
    other_users_booking_id = run_db(
        lambda session: seed_booking(
            session, state=BookingState.PENDING_USER_CONFIRMATION, expires_in_minutes=30
        )
    )
    older_booking_id = _request_booking(client)
    newer_booking_id = _request_booking(client)
    assert client.post(f"/api/bookings/{newer_booking_id}/confirm").status_code == 200

    response = client.get("/api/bookings")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    bookings = response.json()
    booking_ids = [booking["id"] for booking in bookings]
    assert other_users_booking_id not in booking_ids, (
        "GET /api/bookings must scope to the authenticated user (seed_booking owns its own ad-hoc "
        f"user), not return every booking in the database; got {booking_ids}"
    )
    assert booking_ids == [newer_booking_id, older_booking_id], (
        f"expected both owned bookings newest-first, got {booking_ids}"
    )

    confirmed = bookings[0]
    assert [
        (transition["from_state"], transition["to_state"], transition["reason"])
        for transition in confirmed["transitions"]
    ] == [("PENDING_USER_CONFIRMATION", "CONFIRMED", "confirm")], (
        "the confirmed booking must carry the real human-approval transition the state machine "
        f"wrote, so the tab can prove who approved what; got {confirmed['transitions']}"
    )
    assert confirmed["transitions"][0]["actor_user_id"] is not None, (
        "a human confirm must record the acting user — a null actor means the system expired it, "
        "which is the distinction the approval trail exists to preserve"
    )
    assert confirmed["transitions"][0]["actor_email"] == DEMO_USER_EMAIL, (
        "the acting principal's identity must be resolved server-side so the audit UI names who "
        f"decided instead of rendering a raw row id; got {confirmed['transitions'][0]}"
    )
    assert bookings[1]["transitions"] == [], (
        "a booking still awaiting approval has made no decisions yet, so its trail must be empty "
        f"rather than fabricated; got {bookings[1]['transitions']}"
    )
