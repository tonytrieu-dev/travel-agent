"""Trip route boundaries that are easy to regress without an endpoint-level check."""

from tests.db_helpers import get_trip, run_db, seed_trip


def test_get_trip_returns_the_persisted_trip(client) -> None:
    trip_id = run_db(lambda session: seed_trip(session, depart_date="2026-09-01"))

    response = client.get(f"/api/trips/{trip_id}")

    assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["id"] == trip_id and body["origin"] == "JFK" and body["destination_airport"] == "CDG", (
        f"GET /api/trips/{{id}} must return the persisted trip in TripRequestOut shape, got {body}"
    )


def test_patch_nulling_a_required_field_is_422_not_500(client) -> None:
    """Sending an explicit null for a required field used to reach the DB and 500 on the NOT NULL
    insert (or silently corrupt the trip). It must be rejected at the boundary as a 422, and the
    stored value must be left untouched."""
    trip_id = run_db(lambda session: seed_trip(session, depart_date="2026-09-01"))

    response = client.patch(f"/api/trips/{trip_id}", json={"origin": None})

    assert response.status_code == 422, f"expected 422, got {response.status_code}: {response.text}"
    assert response.json()["code"] == "validation_error"
    trip = run_db(lambda session: get_trip(session, trip_id))
    assert trip.origin == "JFK", (
        f"a rejected PATCH must not persist the null origin, found {trip.origin!r}"
    )
