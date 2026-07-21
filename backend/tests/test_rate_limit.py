"""Guards the per-IP rate limit on the two quota-spending routes: the real Nth+1 request over
the real client, not a value asserted against the limiter's own internals.
"""

from app.config import RATE_LIMIT_MAX_REQUESTS
from tests.db_helpers import run_db, seed_trip


def test_flights_search_is_rate_limited_after_max_requests_from_one_client(client) -> None:
    trip_id = run_db(lambda session: seed_trip(session))
    url = f"/api/trips/{trip_id}/flights/search"

    responses = [client.post(url) for _ in range(RATE_LIMIT_MAX_REQUESTS + 1)]

    statuses = [response.status_code for response in responses]
    assert statuses[:RATE_LIMIT_MAX_REQUESTS] == [200] * RATE_LIMIT_MAX_REQUESTS, (
        f"the first {RATE_LIMIT_MAX_REQUESTS} requests must succeed (well under the cap), "
        f"got {statuses[:RATE_LIMIT_MAX_REQUESTS]}"
    )
    last_response = responses[-1]
    assert last_response.status_code == 429, (
        f"request {RATE_LIMIT_MAX_REQUESTS + 1} exceeds the per-IP cap and must be rejected, "
        f"got {last_response.status_code}: {last_response.text}"
    )
    assert last_response.json()["code"] == "rate_limit_exceeded"
    assert "Retry-After" in last_response.headers, (
        "a 429 must tell the client when it's safe to retry"
    )
