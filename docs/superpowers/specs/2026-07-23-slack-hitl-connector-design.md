# Slack HITL connector — design

## Goal

Let a human confirm a flight booking from Slack, not just from the app's own UI —
demonstrating that the approval gate is a channel-agnostic surface over the existing
state machine, not a redesign of it. Explicitly scoped to booking approvals only
(see "Out of scope").

## Why this is safe to add

The state machine in `app/state.py` (`ALLOWED_TRANSITIONS`) is the single source of
truth for what transitions are legal; `booking_repository.py` is the only code that
writes booking state, and every write lands in the same transaction as an immutable
`BookingTransition` audit row. Slack becomes a second *caller* of the same
`confirm_booking` / `execute_booking_durable` / `cancel_booking` functions the REST
routes already call — no new transition logic, no new write path.

The button's `value` is a `booking_log_id` the server itself embedded when building
the card. Slack never sends free text back to the app; the only input from Slack is
"which button, for which id, clicked by which user." There is no prompt-injection
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
Card posted to #approvals channel — Confirm & Book / Reject buttons,
value = booking_log_id

        ⋯ human clicks a button in Slack ⋯

POST /api/slack/events  (Slack's signed interactivity payload)
        │
        ▼
chat.webhooks["slack"](request)  — chat-sdk verifies signature, parses block_actions
        │
        ▼
@chat.on_action("confirm_booking" | "reject_booking") handler
        │  opens a session via get_session_factory() (same pattern dbos_runtime.py uses)
        ▼
resolve_confirm() / resolve_reject()  — plain functions, call the SAME
repository.confirm_booking / execute_booking_durable / repository.cancel_booking
        │
        ▼
handler edits the original Slack message: outcome + who clicked,
buttons removed (prevents double-click races)
```

## Components

### `app/adapters/slack_hitl.py` (new)

Follows the existing adapter pattern (`activities_tavily.py`, `flights_searchapi.py`):
owns all Slack I/O, tolerant of failures — a Slack outage must never turn a
successful booking request into a 500.

- `build_approval_card(trip, flight, booking) -> CardElement` — pure function,
  returns the `chat_sdk.cards.Card(...)` dict. Unit-testable with no network.
- `notify_pending_approval(chat, booking, trip, flight) -> None` — posts the card
  to the configured channel via `chat.channel(f"slack:{channel_id}").post(...)`.
  Catches and logs any failure; never raises into the booking request path.
- `resolve_confirm(session, booking_log_id, actor_display_name) -> str` — calls
  `repository.confirm_booking` then `dbos_runtime.execute_booking_durable`; catches
  `BookingError` (e.g. already confirmed from the frontend — the existing 409
  `invalid_transition`) and returns a human-readable outcome string instead of
  raising. Pure w.r.t. Slack — no `ActionEvent` dependency, so it's testable with
  the same fixtures as existing booking-repository tests.
- `resolve_reject(session, booking_log_id, actor_display_name) -> str` — same
  shape, calls `repository.cancel_booking`.
- `register_handlers(chat) -> None` — thin `@chat.on_action(...)` wrappers: extract
  `booking_log_id` from `ActionEvent.value`, `actor_display_name` from
  `ActionEvent.user`, call the resolve_* function above, then `edit()` the
  original message (via `ActionEvent.thread` / `message_id`) to show the outcome
  and remove the buttons.

  **Note on identity**: `actor_display_name` is Slack-side only — it's used
  purely to render "Confirmed by @alice" in the edited Slack message. It is
  *not* written to `BookingTransition.actor_user_id`, because that column is a
  foreign key to `user_account` and neither `confirm_booking` nor
  `cancel_booking` currently accept an actor parameter at all (the app has no
  Slack-identity-to-`user_account` mapping today, frontend or otherwise).
  Building one is out of scope here — it would be a standing identity/auth
  decision, not something this connector should introduce as a side effect.
- `build_chat_or_none(settings) -> Chat | None` — constructs the `chat_sdk.Chat`
  with `create_slack_adapter(SlackAdapterConfig(bot_token=..., signing_secret=...))`
  if `settings.slack_bot_token` / `slack_signing_secret` / `slack_approvals_channel_id`
  are all set; otherwise returns `None`. Called once at app startup.

### Card content

```python
Card(
    title="✈️ Flight approval needed",
    subtitle=f"Trip #{trip.id} · {origin} → {destination}",
    children=[
        Fields([
            Field(label="Route", value=f"{origin} → {destination_airport}"),
            Field(label="Carrier", value=flight.carrier),
            Field(label="Price", value=f"${flight.price_usd:,.2f} {flight.currency}"),
            Field(label="Departs", value=flight.depart_at),
            Field(label="Stops", value="Nonstop" if flight.stops == 0 else f"{flight.stops} stop(s)"),
            Field(label="Price hold expires", value=booking.expires_at),
        ]),
        Divider(),
        Text("Approve to confirm and book this fare, or reject to release the hold.", style="muted"),
        Actions([
            Button(id="confirm_booking", label="Confirm & Book", style="primary", value=str(booking.id)),
            Button(id="reject_booking", label="Reject", style="danger", value=str(booking.id)),
        ]),
    ],
)
```

After resolution, the handler replaces the `Actions` block with a `Text` line:
`"✅ Confirmed & booked by @alice · 3:42 PM"`, `"✖️ Rejected by @bob"`, or
`"⚠️ Already handled — current state: CONFIRMED"` for the race case.

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

In `create_app()`, if `build_chat_or_none(settings)` returns a `Chat`, mount:

```python
@app.post("/api/slack/events")
async def slack_events(request: Request):
    return await chat.webhooks["slack"](request)
```

If it returns `None`, the route isn't mounted at all — hitting it 404s, which is
the correct signal that Slack isn't configured on this deployment.

### Connector toggle

**New table** `connector_setting` (single row): `slack_enabled: bool = False`.
New repository functions `get_slack_enabled(session)` / `set_slack_enabled(session, enabled)`.

**New routes** (`app/routes/connectors.py`):
- `GET /api/connectors` → `{"slack": {"configured": bool, "enabled": bool}}`.
  `configured` reflects whether the three env vars are set; `enabled` reflects the
  DB row.
- `PATCH /api/connectors/slack` → body `{"enabled": bool}`; 409 if not `configured`.

`request_booking` checks `configured and enabled` (one query) before calling
`notify_pending_approval` — so flipping the toggle off is provably silent, not
just theoretically so.

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

## Testing

- `test_slack_hitl.py`:
  - `build_approval_card` — asserts title/fields/button ids/values, no network.
  - `resolve_confirm` / `resolve_reject` — against the real test DB and
    `booking_repository`, same fixtures as existing booking tests. Covers the
    already-confirmed race (second resolve call returns the "already handled"
    string instead of raising).
- `test_connectors_routes.py` — toggle persists across GET after PATCH, 409 when
  unconfigured, mirrors existing route-test conventions.
- Not tested: chat-sdk's own webhook signature verification / `block_actions`
  parsing — that's the SDK's tested responsibility, not ours to re-verify.

## Slack app setup (manual, one-time, documented separately)

1. Create app at api.slack.com/apps in the target workspace.
2. OAuth & Permissions → Bot Token Scopes → `chat:write`. Install to workspace →
   copy the Bot User OAuth Token (`xoxb-...`).
3. Basic Information → copy the Signing Secret.
4. Create/choose the approvals channel, `/invite @YourBot`, copy its Channel ID.
5. Interactivity & Shortcuts → enable → Request URL =
   `https://<ngrok-id>.ngrok.io/api/slack/events`.
6. `ngrok http 8000`; set `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`,
   `SLACK_APPROVALS_CHANNEL_ID` in `.env`; restart backend; flip the Connectors
   toggle on.

## Dependency

Add `chat-sdk[slack]` to `backend/pyproject.toml`.
