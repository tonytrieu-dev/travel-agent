"""Proves the one thing the Connectors toggle exists for: flipping it off must provably stop
the outbound Slack notification, not just theoretically stop it."""

from unittest.mock import AsyncMock

import pytest

from tests.db_helpers import run_db, seed_flight_search_results, seed_trip


def test_request_booking_does_not_notify_when_connector_disabled(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    notify_mock = AsyncMock()
    monkeypatch.setattr("app.routes.booking.notify_pending_approval", notify_mock)

    trip_id = run_db(lambda session: seed_trip(session))
    flight_ids = run_db(lambda session: seed_flight_search_results(session, trip_id))

    response = client.post(
        f"/api/trips/{trip_id}/booking/request",
        json={"flight_search_result_id": flight_ids[0]},
    )

    assert response.status_code == 200, response.text
    notify_mock.assert_not_called()


def test_request_booking_does_not_notify_when_configured_but_toggle_left_disabled(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct from the 'disabled' test above, which never sets Slack env vars and so only
    proves the *configured* gate short-circuits. This proves the *enabled* gate (the
    connector_setting DB row, the entire reason the toggle exists) independently — Slack is
    fully configured here, but the toggle is never PATCHed to True, so it must still stay off."""
    from app import config

    settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
        slack_bot_token="xoxb-test", slack_signing_secret="secret",
        slack_approvals_channel_id="C123",
    )
    monkeypatch.setattr("app.routes.booking.get_settings", lambda: settings)
    notify_mock = AsyncMock()
    monkeypatch.setattr("app.routes.booking.notify_pending_approval", notify_mock)

    trip_id = run_db(lambda session: seed_trip(session))
    flight_ids = run_db(lambda session: seed_flight_search_results(session, trip_id))

    response = client.post(
        f"/api/trips/{trip_id}/booking/request",
        json={"flight_search_result_id": flight_ids[0]},
    )

    assert response.status_code == 200, response.text
    notify_mock.assert_not_called()


def test_request_booking_notifies_when_connector_enabled_and_configured(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config

    settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
        slack_bot_token="xoxb-test", slack_signing_secret="secret",
        slack_approvals_channel_id="C123",
    )
    # Both routes call get_settings() independently — the PATCH that enables the connector
    # (via app.routes.connectors) and request_booking's own check (via app.routes.booking) —
    # so both must see the configured settings, or the PATCH 409s against the real environment.
    monkeypatch.setattr("app.routes.booking.get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.connectors.get_settings", lambda: settings)
    notify_mock = AsyncMock()
    monkeypatch.setattr("app.routes.booking.notify_pending_approval", notify_mock)

    trip_id = run_db(lambda session: seed_trip(session))
    flight_ids = run_db(lambda session: seed_flight_search_results(session, trip_id))
    enable_response = client.patch("/api/connectors/slack", json={"enabled": True})
    assert enable_response.status_code == 200, enable_response.text

    response = client.post(
        f"/api/trips/{trip_id}/booking/request",
        json={"flight_search_result_id": flight_ids[0]},
    )

    assert response.status_code == 200, response.text
    notify_mock.assert_awaited_once()
