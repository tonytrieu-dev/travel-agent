"""Slack interactivity callback for the booking-approval message. Verifies Slack's webhook
signature before doing anything else, then approves or rejects the booking named by the
clicked button. Never executes a booking here — see docs/superpowers/specs for why.
"""

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.adapters.slack_hitl import (
    parse_block_action,
    resolve_approve,
    resolve_reject,
    update_approval_message,
    verify_slack_signature,
)
from app.config import get_settings
from app.db import get_session_factory
from app.schemas import SlackAuthErrorOut

router = APIRouter(prefix="/api/slack", tags=["slack"])

_UNCONFIGURED_OR_UNSIGNED = JSONResponse(
    status_code=401, content={"detail": "Slack is not configured or the signature is invalid."}
)


@router.post(
    "/interactions",
    responses={401: {"model": SlackAuthErrorOut}},
)
async def slack_interactions(request: Request) -> Response:
    settings = get_settings()
    if not (
        settings.slack_bot_token
        and settings.slack_signing_secret
        and settings.slack_approvals_channel_id
    ):
        return _UNCONFIGURED_OR_UNSIGNED

    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(
        raw_body, timestamp, signature, settings.slack_signing_secret.get_secret_value()
    ):
        return _UNCONFIGURED_OR_UNSIGNED

    form = await request.form()
    payload_raw = form.get("payload")
    if not isinstance(payload_raw, str):
        return Response(status_code=200)
    try:
        payload: dict[str, Any] = json.loads(payload_raw)
    except json.JSONDecodeError:
        return Response(status_code=200)

    parsed = parse_block_action(payload, expected_channel_id=settings.slack_approvals_channel_id)
    if parsed is None:
        return Response(status_code=200)
    action_id, booking_log_id = parsed

    async with get_session_factory()() as session:
        if action_id == "approve_booking":
            outcome = await resolve_approve(session, booking_log_id)
        else:
            outcome = await resolve_reject(session, booking_log_id)

    await update_approval_message(settings, payload, outcome)
    return Response(status_code=200)
