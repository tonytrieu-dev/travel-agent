"""Slack HITL connector: signature verification, payload parsing, approve/reject resolution,
and the outbound notification — each tested at the level that would actually catch a regression,
not duplicated across unit and route layers (see test_slack_interactions_route.py for the
end-to-end signed-request path).
"""

import asyncio
from typing import Any

import httpx
import pytest
from app.adapters.slack_hitl import (
    build_approval_blocks,
    notify_pending_approval,
    parse_block_action,
    resolve_approve,
    resolve_reject,
    verify_slack_signature,
)
from app.config import Settings
from app.models import FlightSearchResult, HITLBookingLog, TripRequest
from app.state import BookingState
from sqlalchemy.ext.asyncio import AsyncSession
from tests.db_helpers import get_booking, run_db, seed_booking

# Slack's own documented example (https://docs.slack.dev/authentication/verifying-requests-from-slack/).
# Note: the trailing "5" on the secret is part of Slack's real published value — dropping it
# (an easy transcription slip) makes this test fail even with a correct HMAC implementation.
_SLACK_DOC_SIGNING_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
_SLACK_DOC_TIMESTAMP = "1531420618"
_SLACK_DOC_BODY = (
    b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&"
    b"channel_id=G8PSS9T3V&channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner&"
    b"command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com%2F"
    b"commands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&"
    b"trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c"
)
_SLACK_DOC_SIGNATURE = (
    "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503"
)
# Freshness check compares against `now`; pin it to the example's own timestamp so this test
# exercises only the HMAC computation, not the replay window (that's a separate test below).
_SLACK_DOC_NOW = float(_SLACK_DOC_TIMESTAMP)


def test_verify_slack_signature_accepts_slacks_own_documented_example() -> None:
    """Regression guard against a wrong base-string/encoding: if this ever fails, every real
    Slack request will also fail verification, silently locking the connector out."""
    assert verify_slack_signature(
        _SLACK_DOC_BODY,
        _SLACK_DOC_TIMESTAMP,
        _SLACK_DOC_SIGNATURE,
        _SLACK_DOC_SIGNING_SECRET,
        now=_SLACK_DOC_NOW,
    )


def test_verify_slack_signature_rejects_a_tampered_body() -> None:
    """A signature computed for one body must not verify against a different one — otherwise an
    attacker who captured one valid request could replay it with an arbitrary payload."""
    assert not verify_slack_signature(
        _SLACK_DOC_BODY + b"&tampered=1",
        _SLACK_DOC_TIMESTAMP,
        _SLACK_DOC_SIGNATURE,
        _SLACK_DOC_SIGNING_SECRET,
        now=_SLACK_DOC_NOW,
    )


def test_verify_slack_signature_rejects_a_stale_timestamp() -> None:
    """Slack's replay-protection contract requires rejecting anything older than 5 minutes, even
    with a mathematically valid signature — otherwise a captured request stays replayable forever."""
    assert not verify_slack_signature(
        _SLACK_DOC_BODY,
        _SLACK_DOC_TIMESTAMP,
        _SLACK_DOC_SIGNATURE,
        _SLACK_DOC_SIGNING_SECRET,
        now=_SLACK_DOC_NOW + 301,
    )


def test_resolve_approve_confirms_a_pending_booking() -> None:
    """Plain sync def, not async def: run_db() calls asyncio.run() internally, which raises
    if invoked from inside an already-running event loop — the same reason every existing test
    in this codebase that uses run_db (see tests/steps/test_booking_hitl.py) is a sync def."""
    log_id = run_db(
        lambda session: seed_booking(
            session, state=BookingState.PENDING_USER_CONFIRMATION, expires_in_minutes=30
        )
    )

    async def _run(session: AsyncSession) -> str:
        return await resolve_approve(session, log_id)

    outcome = run_db(_run)

    assert "continue in the app" in outcome.lower(), (
        f"approving must not claim the flight is purchased, got {outcome!r}"
    )
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.CONFIRMED


def test_resolve_approve_on_an_already_executed_booking_renders_the_conflict_without_raising() -> None:
    """Regression guard: a Slack click can race a frontend click that already executed the same
    booking. resolve_approve must degrade to a message, matching how the REST /confirm endpoint
    already turns this into a 409 rather than crashing the request."""
    log_id = run_db(
        lambda session: seed_booking(session, state=BookingState.EXECUTED, expires_in_minutes=30)
    )

    async def _run(session: AsyncSession) -> str:
        return await resolve_approve(session, log_id)

    outcome = run_db(_run)

    assert "EXECUTED" in outcome, (
        f"the outcome text must be the real BookingError.detail naming the conflicting state, "
        f"got {outcome!r}"
    )


def test_resolve_reject_on_a_terminal_booking_renders_the_conflict_without_raising() -> None:
    log_id = run_db(
        lambda session: seed_booking(session, state=BookingState.EXECUTED, expires_in_minutes=30)
    )

    async def _run(session: AsyncSession) -> str:
        return await resolve_reject(session, log_id)

    outcome = run_db(_run)

    assert "terminal state" in outcome.lower(), (
        f"rejecting an already-executed booking must render cancel_booking's real conflict "
        f"detail, not raise or invent new copy, got {outcome!r}"
    )


def _build_test_blocks() -> dict[str, Any]:
    trip = TripRequest(
        id=1,
        user_id=1,
        origin="JFK",
        destination="Paris",
        destination_airport="CDG",
        depart_date="2026-08-01",
    )
    flight = FlightSearchResult(
        id=1,
        trip_request_id=1,
        offer_index=0,
        carrier="AF",
        price_usd=512.0,
        currency="USD",
        depart_at="2026-08-01T09:00:00",
        arrive_at="2026-08-01T21:30:00",
        stops=0,
        booking_token="tok-abc",
        raw_offer={},
    )
    booking = HITLBookingLog(
        id=1,
        trip_request_id=1,
        flight_search_result_id=1,
        requested_by_user_id=1,
        expires_at=trip.created_at,
    )
    return build_approval_blocks(trip, flight, booking)


def test_approval_blocks_never_claim_the_flight_is_purchased() -> None:
    """Regression guard for the exact bug caught in review: 'Confirm & Book' / 'Confirmed &
    booked' overclaimed what execute_booking actually does (an internal handoff, not a real
    airline purchase — see docs/ARCHITECTURE.md). Approving must read as a step, not a purchase."""
    rendered = str(_build_test_blocks())
    assert "book" not in rendered.lower() or "approve" in rendered.lower(), (
        f"card copy must not read as a purchase confirmation, got: {rendered}"
    )
    assert "purchas" not in rendered.lower(), (
        f"card copy must never claim the flight was purchased, got: {rendered}"
    )


def test_approval_blocks_carry_the_booking_id_as_the_button_value() -> None:
    blocks = _build_test_blocks()
    actions_block = next(b for b in blocks["blocks"] if b["type"] == "actions")
    values = {button["value"] for button in actions_block["elements"]}
    assert values == {"1"}, f"both buttons must carry the booking log id, got {values}"


def _block_actions_payload(*, channel_id: str, action_id: str, value: str) -> dict[str, Any]:
    return {
        "type": "block_actions",
        "channel": {"id": channel_id},
        "actions": [{"action_id": action_id, "value": value}],
    }


def test_parse_block_action_returns_action_and_booking_id_for_a_valid_payload() -> None:
    payload = _block_actions_payload(channel_id="C123", action_id="approve_booking", value="42")
    assert parse_block_action(payload, expected_channel_id="C123") == ("approve_booking", 42)


def test_parse_block_action_rejects_the_wrong_channel() -> None:
    """Channel membership is what grants approval authority in this single-user demo (no
    Slack-identity-to-user mapping exists) — a payload from any other channel must be ignored,
    not silently trusted."""
    payload = _block_actions_payload(channel_id="C_OTHER", action_id="approve_booking", value="42")
    assert parse_block_action(payload, expected_channel_id="C123") is None


def test_parse_block_action_rejects_a_non_integer_value() -> None:
    payload = _block_actions_payload(
        channel_id="C123", action_id="approve_booking", value="not-a-number"
    )
    assert parse_block_action(payload, expected_channel_id="C123") is None


def test_notify_pending_approval_swallows_a_slack_outage_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: a Slack outage or bad token must never turn a successful booking
    request into a 500 for the human waiting on the frontend — matches the tolerant pattern
    every other adapter in this codebase already follows (see activities_tavily.py)."""

    async def _fake_post(self, url, json=None, headers=None) -> httpx.Response:
        raise httpx.ConnectError("simulated Slack outage")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    trip = TripRequest(
        id=1, user_id=1, origin="JFK", destination="Paris",
        destination_airport="CDG", depart_date="2026-08-01",
    )
    flight = FlightSearchResult(
        id=1, trip_request_id=1, offer_index=0, carrier="AF", price_usd=512.0,
        currency="USD", depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:30:00",
        stops=0, booking_token="tok-abc", raw_offer={},
    )
    booking = HITLBookingLog(
        id=1, trip_request_id=1, flight_search_result_id=1,
        requested_by_user_id=1, expires_at=trip.created_at,
    )
    settings = Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url="postgresql+asyncpg://x/x",
        slack_bot_token="xoxb-test", slack_signing_secret="secret",
        slack_approvals_channel_id="C123",
    )

    asyncio.run(notify_pending_approval(settings, booking, trip, flight))
