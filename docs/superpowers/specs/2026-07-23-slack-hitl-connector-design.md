# Slack HITL connector — design

## Goal

Let a human approve a flight booking from Slack, not just from the app's own UI —
demonstrating that the approval gate is a channel-agnostic surface over the existing
state machine, not a redesign of it. Explicitly scoped to booking approvals only
(see "Out of scope").

## Revision note

This is a revision after a review pass (see git history for the prior version).
The review's core finding: the original design over-scoped the dependency (a
9-platform, alpha-status chat SDK for one Slack message and one callback), the
copy ("Confirm & Book") overclaimed what execution actually does, and it executed
the booking inside the Slack interaction request — a real problem, since Slack
requires a response within 3 seconds and execution calls an external provider.
All of that is fixed below. The one point where this revision deliberately
disagrees with the review: the DB-backed Connectors toggle stays, shrunk to its
minimal form, because it's an explicit, twice-confirmed requirement, not a
code-quality question.

## Why this is safe to add

The state machine in `app/state.py` (`ALLOWED_TRANSITIONS`) is the single source of
truth for what transitions are legal; `booking_repository.py` is the only code that
writes booking state, and every write lands in the same transaction as an immutable
`BookingTransition` audit row. Slack becomes a second *caller* of the same
`confirm_booking` / `cancel_booking` functions the REST routes already call — no
new transition logic, no new write path, and (per the fix below) no new execution
path either.

The button's `value` is a `booking_log_id` the server itself embedded when building
the message. Slack never sends free text back to the app; the only input from Slack
is "which button, for which id, from which channel." There is no prompt-injection
surface in this design, because nothing LLM-generated or user-authored ever flows
from Slack into the agent or into a write.

## Out of scope

- **Itinerary generation/display in Slack.** That would mean posting LLM-generated
  content into a channel and, the moment any interactivity is added around it,
  piping untrusted chat input back into the agent — a real injection vector this
  design deliberately avoids. Not requested by the take-home brief either (the
  brief's Slack-relevant ask is specifically "human to confirm flight").
- **General agent interaction via Slack** (searching flights, asking clarifying
  questions, etc. through chat). The brief's UI requirement is about the app's own
  frontend; Slack here is a second delivery channel for one existing control,
  not a second product surface.
- **Multi-workspace / OAuth install flow.** Single workspace, static bot token.
- **Email delivery channel.** Slack only, per scope discussion.
- **Slack-identity-to-user mapping.** `BookingTransition.actor_user_id` continues
  to record `requested_by_user_id`, exactly as it does today for the frontend
  path. The Slack message does not claim a clicker identity the DB doesn't back
  (see "Card content" below) — adding real identity mapping is a standing
  auth decision, not something this connector should introduce as a side effect.
- **Executing the booking from Slack.** Approve only calls `confirm_booking`.
  Execution (which calls SearchApi and can take up to `SEARCHAPI_TIMEOUT_SECONDS`)
  stays behind the frontend's existing Execute action — both because Slack
  requires a 3-second ack and because the frontend already makes clear ("your
  flight hasn't been purchased") what execute does and doesn't do.

## Architecture

```
request_booking (routes/booking.py)
        │
        ▼
repository.request_booking()  ── writes HITLBookingLog(PENDING_USER_CONFIRMATION)
        │
        ▼ (if slack configured AND connector enabled)
slack_hitl.notify_pending_approval(booking, trip, flight)
        │
        ▼
Block Kit message posted to the configured channel via chat.postMessage —
Approve flight / Reject buttons, value = booking_log_id

        ⋯ human clicks a button in Slack ⋯

POST /api/slack/interactions
  (Content-Type: application/x-www-form-urlencoded, body has one field: `payload`)
        │
        ▼
verify_signature(raw_body, headers)  — stdlib hmac/hashlib, Slack's documented
  v0:{timestamp}:{raw_body} scheme, timing-safe compare, 5-minute replay window
        │
        ▼
parse payload → validate type == "block_actions", channel.id == configured
  channel, action_id in {approve_booking, reject_booking}, value is a valid int
        │
        ▼
resolve_approve() / resolve_reject()  — plain functions, call the SAME
repository.confirm_booking / repository.cancel_booking used by the REST routes.
Catch BookingError, render its existing .detail text — no new copy to keep in sync.
        │
        ▼
HTTP response body IS the Slack update: {"replace_original": true, "text": ...,
  "blocks": [...]}  — buttons removed, outcome shown. Satisfies Slack's 3-second
  ack because it's the same synchronous request/response, no follow-up call needed.
```

## Components

### `app/adapters/slack_hitl.py` (new)

Follows the existing adapter pattern (`activities_tavily.py`, `flights_searchapi.py`):
owns all Slack I/O over plain `httpx`, tolerant of failures — a Slack outage must
never turn a successful booking request into a 500. No new dependency: `httpx` is
already used for the SearchApi/Tavily adapters; signature verification is ~10 lines
of stdlib `hmac`/`hashlib`. (A 9-platform, alpha-status chat SDK was considered and
dropped — the actual need is one Block Kit POST and one signed callback, and
pulling in cross-platform card translation, concurrency primitives, and multiple
state backends for that is more surface than the feature warrants.)

- `build_approval_blocks(trip, flight, booking) -> dict` — pure function, returns
  the Block Kit `blocks` array (header, a `section` with `fields` for route/
  carrier/price/departs/expiry, a `divider`, an `actions` block with the two
  buttons). Unit-testable with no network.
- `notify_pending_approval(settings, booking, trip, flight) -> None` — `httpx`
  POST to `https://slack.com/api/chat.postMessage` with
  `Authorization: Bearer {bot_token}`. Catches and logs any failure; never raises
  into the booking request path.
- `verify_slack_signature(raw_body, timestamp, signature, signing_secret) -> bool`
  — implements Slack's documented `v0:{timestamp}:{raw_body}` HMAC-SHA256 scheme,
  `hmac.compare_digest` for the comparison, rejects timestamps more than 5 minutes
  old. Pure, no I/O — directly unit-testable against Slack's own documented
  example values.
- `resolve_approve(session, booking_log_id) -> str` — calls
  `repository.confirm_booking`; on `BookingError`, returns `error.detail` instead
  of raising. Returns a short outcome string for the updated message.
- `resolve_reject(session, booking_log_id) -> str` — same shape, calls
  `repository.cancel_booking`.

### `POST /api/slack/interactions` (new route, `app/routes/slack.py`)

1. Read the raw request body (needed for signature verification — must happen
   before any form-parsing that could alter it).
2. `verify_slack_signature(...)`; reject with 401 on failure.
3. Parse the `payload` form field as JSON. Validate `type == "block_actions"`,
   exactly one action, `channel.id == settings.slack_approvals_channel_id`
   (defense noted by review: only act on interactions from the configured
   channel — channel membership is what grants approval authority in this
   single-user demo, so this check matters), `action_id` is one of the two known
   IDs, and `value` parses as an integer. Malformed payloads get a 200 with a
   generic "couldn't process that action" message — Slack expects 200s even for
   handled failures, to avoid retries.
4. Open a session via `get_session_factory()` (same pattern `dbos_runtime.py`
   already uses for out-of-request DB access), call `resolve_approve` /
   `resolve_reject`.
5. Return `{"replace_original": true, "text": outcome, "blocks": [...]}` — the
   original `actions` block replaced by a `context` block showing the outcome
   text. No clicker identity is shown (see "Out of scope"); no follow-up network
   call is needed since this response body itself updates the message.

### Card content

Approve/Reject buttons; copy corrected per review — execution doesn't purchase a
flight, it hands off to the airline:

```python
{
    "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": "Flight approval needed"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Trip:*\n#{trip.id}"},
            {"type": "mrkdwn", "text": f"*Route:*\n{trip.origin} → {trip.destination_airport}"},
            {"type": "mrkdwn", "text": f"*Carrier:*\n{flight.carrier}"},
            {"type": "mrkdwn", "text": f"*Price:*\n${flight.price_usd:,.2f} {flight.currency}"},
            {"type": "mrkdwn", "text": f"*Departs:*\n{flight.depart_at}"},
            {"type": "mrkdwn", "text": f"*Price hold expires:*\n{booking.expires_at}"},
        ]},
        {"type": "divider"},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Approve to confirm this fare, or reject to release the hold. Approving does not purchase the flight — you'll continue in the app to complete it with the airline."},
        ]},
        {"type": "actions", "block_id": "booking_actions", "elements": [
            {"type": "button", "action_id": "approve_booking", "style": "primary",
             "text": {"type": "plain_text", "text": "Approve flight"}, "value": str(booking.id)},
            {"type": "button", "action_id": "reject_booking", "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"}, "value": str(booking.id)},
        ]},
    ],
}
```

Outcome text after resolution is whatever `resolve_approve`/`resolve_reject`
returns — either a plain "Approved — continue in the app to complete booking
with the airline." / "Rejected — the fare hold has been released." on success,
or the real `BookingError.detail` text on conflict (e.g. "Cannot confirm a
booking in state EXECUTED.") — reusing the API's own error text rather than
inventing parallel copy that could drift from it.

### Config (`app/config.py`)

New optional settings, following the existing `X | None = None` convention for
feature-flagged integrations:

```python
slack_bot_token: SecretStr | None = None
slack_signing_secret: SecretStr | None = None
slack_approvals_channel_id: str | None = None
```

"Configured" = all three set. Wrapping in `SecretStr` matches `cerebras_api_key` etc.

### `app/main.py` wiring

`POST /api/slack/interactions` is always mounted (it's a thin, cheap route); it
502s/no-ops gracefully if Slack isn't configured, since `notify_pending_approval`
already checks configuration before ever posting, so no interaction should exist
to receive in that case. Keeps the wiring unconditional and simple rather than
branching route registration on config.

### Connector toggle (kept, minimal)

Explicit, twice-confirmed requirement — kept, but shrunk: no separate repository
module, no dedicated test file beyond what the two routes need.

**New table** `connector_setting` (single row): `slack_enabled: bool = False`.

**New routes** (`app/routes/connectors.py`, direct queries, no repository layer):
- `GET /api/connectors` → `{"slack": {"configured": bool, "enabled": bool}}`.
  `configured` reflects whether the three env vars are set; `enabled` reflects the
  DB row.
- `PATCH /api/connectors/slack` → body `{"enabled": bool}`; 409 if not `configured`.

`request_booking` checks `configured and enabled` (one query) before calling
`notify_pending_approval` — so flipping the toggle off is provably silent, not
just theoretically so, which is the actual point of having a live toggle instead
of an env-var-only flag.

### Frontend: Connectors tab

- `App.tsx`: `TabKey = "trip" | "execution" | "connectors"`, new entry in `TABS`.
- New `ConnectorsPanel.tsx`: fetches `GET /api/connectors` on mount, renders a
  Slack row with a toggle switch calling `PATCH /api/connectors/slack` on change,
  optimistic-updates then reconciles with the response. If `configured: false`,
  the switch is disabled/greyed with "Slack credentials not configured on the
  server" beneath it.
- `api/types.ts` / `api/client.ts`: add `ConnectorsOut` type and
  `getConnectors()` / `setSlackConnectorEnabled(enabled)` client functions,
  matching the existing typed-client pattern.

## Contract-first workflow (per `AGENTS.md`)

This repo's documented process is `specs/openapi.yaml` (contract) →
`features/*.feature` (Gherkin) → red → green. Missed in the first pass of this
design; the implementation plan must do, in order:

1. Add `POST /api/slack/interactions`, `GET /api/connectors`, and
   `PATCH /api/connectors/slack` to `backend/specs/openapi.yaml`.
2. Add a Gherkin scenario to `backend/features/booking_hitl.feature` covering:
   signed approval succeeds and moves the booking to `CONFIRMED`; invalid
   signature is rejected; wrong channel ID is rejected.
3. Red → green from there, consistent with how the rest of the booking flow was
   built.

## Testing

- `test_slack_hitl.py`:
  - `build_approval_blocks` — asserts block structure/button ids/values, no
    network, and asserts the copy does not claim purchase.
  - `verify_slack_signature` — against Slack's own documented example
    request/signature pair, plus a tampered-body and an expired-timestamp case.
  - `resolve_approve` / `resolve_reject` — against the real test DB and
    `booking_repository`, same fixtures as existing booking tests. Covers the
    real conflict case (e.g. approve arrives after the frontend already
    executed) by asserting the returned string matches `BookingError.detail`.
- `test_slack_interactions_route.py` (or as Gherkin steps per above): signed
  `block_actions` payload confirms a booking; invalid signature → 401; wrong
  channel ID → rejected without touching the booking.
- Connectors routes: covered by the Gherkin scenario / a small route test,
  toggle persists across GET after PATCH, 409 when unconfigured.
- Not tested: Slack's own delivery guarantees or retry behavior — only our
  verification and resolution logic.

## Slack app setup (manual, one-time, documented separately)

1. Create app at api.slack.com/apps in the target workspace.
2. OAuth & Permissions → Bot Token Scopes → `chat:write`. Install to workspace →
   copy the Bot User OAuth Token (`xoxb-...`).
3. Basic Information → copy the Signing Secret.
4. Create/choose the approvals channel, `/invite @YourBot`, copy its Channel ID.
5. Interactivity & Shortcuts → enable → Request URL =
   `https://<ngrok-id>.ngrok.io/api/slack/interactions`.
6. `ngrok http 8000`; set `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`,
   `SLACK_APPROVALS_CHANNEL_ID` in `.env`; restart backend; flip the Connectors
   toggle on.

## Dependency

None new. `httpx` (already a dependency) for the outbound POST; stdlib `hmac`/
`hashlib` for signature verification.
