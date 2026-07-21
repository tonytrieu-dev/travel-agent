"""A malformed request body must fail the same shape as every domain rejection (ProblemDetail),
not FastAPI's default `{"detail": [...]}` array — a client that only handles one error contract
must not need a special case for the one kind of failure most likely to happen first (bad input).
"""


def test_malformed_request_body_returns_a_problem_detail_not_the_fastapi_default(client) -> None:
    response = client.post(
        "/api/trips/2/booking/request", json={"flight_search_result_id": "not-an-integer"}
    )

    assert response.status_code == 422, (
        f"a type-invalid body must still 422, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body.get("code") == "validation_error", (
        f"expected the app's own ProblemDetail shape (code='validation_error'), got {body} — "
        "FastAPI's default validation-error handler is still in effect"
    )
    assert "flight_search_result_id" in body.get("detail", ""), (
        f"detail must name the offending field so a developer doesn't have to guess which one "
        f"failed, got {body.get('detail')!r}"
    )
