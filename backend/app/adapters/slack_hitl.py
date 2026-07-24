"""Slack HITL connector adapter: builds the Block Kit approval message, verifies Slack's
webhook signature, and resolves an approve/reject click against the real booking state machine.
No SDK — the surface is one outbound POST and one signed inbound callback, well within what
httpx + stdlib hmac covers on their own.
"""

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx
from app.config import SLACK_API_TIMEOUT_SECONDS, Settings
from app.models import FlightSearchResult, HITLBookingLog, TripRequest
from app.repositories import booking_repository as repository
from app.repositories.booking_repository import BookingError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SLACK_SIGNATURE_MAX_AGE_SECONDS = 5 * 60
_APPROVE_ACTION_ID = "approve_booking"
_REJECT_ACTION_ID = "reject_booking"


def _compute_slack_signature(raw_body: bytes, timestamp: str, signing_secret: str) -> str:
    base_string = f"v0:{timestamp}:".encode() + raw_body
    digest = hmac.new(signing_secret.encode(), base_string, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def verify_slack_signature(
    raw_body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
    *,
    now: float | None = None,
) -> bool:
    try:
        timestamp_seconds = int(timestamp)
    except ValueError:
        return False
    current_time = now if now is not None else time.time()
    if abs(current_time - timestamp_seconds) > SLACK_SIGNATURE_MAX_AGE_SECONDS:
        return False
    expected = _compute_slack_signature(raw_body, timestamp, signing_secret)
    return hmac.compare_digest(expected, signature)


async def resolve_approve(session: AsyncSession, booking_log_id: int) -> str:
    try:
        await repository.confirm_booking(session, booking_log_id)
    except BookingError as error:
        return error.detail
    return "Approved — continue in the app to complete booking with the airline."


async def resolve_reject(session: AsyncSession, booking_log_id: int) -> str:
    try:
        await repository.cancel_booking(session, booking_log_id)
    except BookingError as error:
        return error.detail
    return "Rejected — this fare will not be booked."


def build_approval_blocks(
    trip: TripRequest, flight: FlightSearchResult, booking: HITLBookingLog
) -> dict[str, Any]:
    stops_text = "Nonstop" if flight.stops == 0 else f"{flight.stops} stop(s)"
    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Flight approval needed"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Trip:*\n#{trip.id}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Route:*\n{trip.origin} → {trip.destination_airport}",
                    },
                    {"type": "mrkdwn", "text": f"*Carrier:*\n{flight.carrier}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Price:*\n${flight.price_usd:,.2f} {flight.currency}",
                    },
                    {"type": "mrkdwn", "text": f"*Departs:*\n{flight.depart_at}"},
                    {"type": "mrkdwn", "text": f"*Stops:*\n{stops_text}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Approval expires:*\n{booking.expires_at.isoformat()}",
                    },
                ],
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Approve to confirm this fare, or reject to cancel the request. "
                            "Approving does not complete the booking — you'll continue in "
                            "the app to finish it with the airline."
                        ),
                    }
                ],
            },
            {
                "type": "actions",
                "block_id": "booking_actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": _APPROVE_ACTION_ID,
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve flight"},
                        "value": str(booking.id),
                    },
                    {
                        "type": "button",
                        "action_id": _REJECT_ACTION_ID,
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "value": str(booking.id),
                    },
                ],
            },
        ],
    }


def build_resolution_blocks(outcome_text: str) -> dict[str, Any]:
    return {
        "replace_original": True,
        "text": outcome_text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": outcome_text}},
        ],
    }


def parse_block_action(
    payload: dict[str, Any], expected_channel_id: str
) -> tuple[str, int] | None:
    if payload.get("type") != "block_actions":
        return None
    if payload.get("channel", {}).get("id") != expected_channel_id:
        return None
    actions = payload.get("actions") or []
    if len(actions) != 1:
        return None
    action_id = actions[0].get("action_id")
    if action_id not in (_APPROVE_ACTION_ID, _REJECT_ACTION_ID):
        return None
    try:
        booking_log_id = int(actions[0].get("value"))
    except (TypeError, ValueError):
        return None
    return action_id, booking_log_id


async def notify_pending_approval(
    settings: Settings, booking: HITLBookingLog, trip: TripRequest, flight: FlightSearchResult
) -> None:
    assert settings.slack_bot_token is not None
    assert settings.slack_approvals_channel_id is not None
    blocks = build_approval_blocks(trip, flight, booking)
    try:
        async with httpx.AsyncClient(timeout=SLACK_API_TIMEOUT_SECONDS) as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {settings.slack_bot_token.get_secret_value()}"
                },
                json={"channel": settings.slack_approvals_channel_id, **blocks},
            )
    except httpx.HTTPError as error:
        logger.warning("slack notify_pending_approval failed: %r for booking=%r", error, booking.id)
