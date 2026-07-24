# Slack HITL Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human approve a pending flight booking from a Slack message (Approve/Reject buttons), reusing the existing `confirm_booking`/`cancel_booking` state-machine functions, gated behind a DB-backed toggle visible in a new "Connectors" frontend tab.

**Architecture:** `app/adapters/slack_hitl.py` owns all Slack I/O (plain `httpx` + stdlib `hmac`, no SDK) — building the Block Kit approval message, verifying Slack's webhook signature, and resolving an approve/reject click by calling the real repository functions. `POST /api/slack/interactions` receives the signed callback; `request_booking` fires the outbound notification when a small `connector_setting` DB row says Slack is enabled and the three Slack env vars are present. A new Connectors tab in the frontend reads/writes that toggle.

**Tech Stack:** FastAPI, SQLModel/Alembic, `httpx`, stdlib `hmac`/`hashlib`, pytest + pytest-bdd (existing conventions), React/TypeScript (existing conventions).

## Global Constraints

- No new dependency. Use `httpx` (already a dependency) and stdlib `hmac`/`hashlib` — not `chat-sdk`.
- Approve only calls `confirm_booking`. Never call `execute_booking`/`execute_booking_durable` from the Slack interaction path (Slack's 3-second ack requirement; execution calls an external provider).
- Never claim the flight has been purchased. Approved copy: "Approve flight" / "Approved — continue in the app to complete booking with the airline." Never "Confirm & Book" / "Confirmed & booked."
- Never display a Slack clicker's identity in the message. `BookingTransition.actor_user_id` keeps recording `requested_by_user_id`, unchanged from today.
- On conflict (`BookingError`), render `error.detail` verbatim — do not invent parallel copy.
- Contract-first: `specs/openapi.yaml` changes land before the routes that implement them (per `AGENTS.md`), then a Gherkin scenario, then the route.
- Test only what's a genuine regression guard, edge case, or the feature's core user-visible behavior — no test for a trivial function whose failure would be obvious from a one-line read. Every new test's assert carries a message pointing at *why*, matching this repo's existing style (see `tests/steps/test_booking_hitl.py`).
- Follow existing conventions exactly: `SecretStr` for env secrets, `X | None = None` for feature-flagged settings, `ProblemDetail`/`ErrorCode` for app-facing errors, `get_session_factory()` for out-of-request DB access, `responses=` dicts on routes mirroring `routes/booking.py`.

---

## Task 1: Backend schema — Slack settings + `connector_setting` table

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/alembic/versions/a3f6d2c81e97_add_connector_setting_table.py`
- Modify: `backend/app/models.py`
- Modify: `.env.example`
- Modify: `backend/tests/conftest.py:35-38` (`_ALL_TABLES`)

**Interfaces:**
- Produces: `Settings.slack_bot_token: SecretStr | None`, `Settings.slack_signing_secret: SecretStr | None`, `Settings.slack_approvals_channel_id: str | None`; `ConnectorSetting` SQLModel table (`connector_setting`, single row, `id`, `slack_enabled: bool`).

- [ ] **Step 1: Add the three optional Slack settings to `Settings`**

In `backend/app/config.py`, add below the existing `frontend_origin` line (inside `class Settings`):

```python
    slack_bot_token: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    slack_approvals_channel_id: str | None = None
```

Also add near the top of the file, alongside `SEARCHAPI_TIMEOUT_SECONDS`:

```python
SLACK_API_TIMEOUT_SECONDS = 10.0
```

- [ ] **Step 2: Add `ConnectorSetting` model**

In `backend/app/models.py`, add after the `HITLBookingLog` class (before the `# ── Audit & observability` section comment):

```python
class ConnectorSetting(SQLModel, table=True):
    """Single-row table: live, DB-backed toggles for optional external connectors (currently
    just Slack). Kept separate from ``Settings`` because it must be flippable at runtime without
    a restart — that's the point of a toggle instead of an env var."""

    __tablename__ = "connector_setting"

    id: int | None = Field(default=None, primary_key=True)
    slack_enabled: bool = Field(default=False)
```

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/a3f6d2c81e97_add_connector_setting_table.py`:

```python
"""add connector_setting table

Revision ID: a3f6d2c81e97
Revises: c2f4a8e9d103
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f6d2c81e97"
down_revision: str | None = "c2f4a8e9d103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slack_enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("connector_setting")
```

- [ ] **Step 4: Apply the migration and confirm the table exists**

Run: `cd backend && uv run alembic upgrade head`
Expected: no errors; `uv run alembic current` prints `a3f6d2c81e97 (head)`.

Also apply it to the test database (the suite runs against `travel_agent_test`, migrated separately):

Run: `cd backend && DATABASE_URL=postgresql+asyncpg://tony@localhost:5432/travel_agent_test uv run alembic upgrade head`
Expected: no errors.

- [ ] **Step 5: Add `connector_setting` to the test-truncation table list**

In `backend/tests/conftest.py:35-38`, the truncate list will otherwise never include the new
table, and a leftover row from one test would leak into the next:

```python
_ALL_TABLES = (
    "booking_transition, execution_event, agent_run_step, agent_run, hitl_booking_log, "
    "itinerary, flight_search_result, trip_request, user_account, connector_setting"
)
```

- [ ] **Step 6: Document the new env vars**

In `.env.example`, add after the `FRONTEND_ORIGIN` line:

```
# Optional — Slack HITL connector. All three must be set for the Connectors tab to allow
# enabling it. See docs/SLACK_SETUP.md for how to get these values.
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SLACK_APPROVALS_CHANNEL_ID=
```

- [ ] **Step 7: Run the full backend suite to confirm nothing broke**

Run: `cd backend && uv run pytest -q`
Expected: all existing tests still pass (this task adds no new tests — it's schema only,
covered indirectly once Task 5/6 exercise the table).

- [ ] **Step 8: Commit**

```bash
git add backend/app/config.py backend/app/models.py backend/alembic/versions/a3f6d2c81e97_add_connector_setting_table.py backend/tests/conftest.py .env.example
git commit -m "feat: add Slack connector settings and connector_setting table"
```

---

## Task 2: OpenAPI contract additions

**Files:**
- Modify: `backend/specs/openapi.yaml`
- Modify: `backend/app/schemas.py` (add `CONNECTOR_NOT_CONFIGURED` to `ErrorCode`)
- Modify: `frontend/src/api/types.ts` (mirror the `ErrorCode` union)

**Interfaces:**
- Produces: contract entries for `POST /api/slack/interactions`, `GET /api/connectors`,
  `PATCH /api/connectors/slack`; schemas `SlackInteractionOut`, `SlackAuthErrorOut`,
  `ConnectorStatusOut`, `ConnectorsOut`, `ConnectorToggleUpdate`; `ErrorCode.CONNECTOR_NOT_CONFIGURED = "connector_not_configured"`.

- [ ] **Step 1: Add the new error code**

In `backend/app/schemas.py`, in `class ErrorCode(StrEnum)`, add:

```python
    CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
```

Mirror it in `frontend/src/api/types.ts`'s `ErrorCode` union:

```typescript
export type ErrorCode =
  | "booking_not_found"
  | "trip_not_found"
  | "flight_not_found"
  | "booking_expired"
  | "invalid_transition"
  | "validation_error"
  | "rate_limit_exceeded"
  | "connector_not_configured"
```

- [ ] **Step 2: Add the new schemas to `openapi.yaml`**

In `backend/specs/openapi.yaml`, under `components.schemas`, add (anywhere among the other
schema entries — alphabetical grouping isn't enforced elsewhere in the file, so add these
after `ExecutionPanelOut`):

```yaml
    SlackInteractionOut:
      type: object
      required: [replace_original, text, blocks]
      properties:
        replace_original: { type: boolean }
        text: { type: string }
        blocks:
          type: array
          items: { type: object, additionalProperties: true }

    SlackAuthErrorOut:
      type: object
      required: [detail]
      properties:
        detail: { type: string }

    ConnectorStatusOut:
      type: object
      required: [configured, enabled]
      properties:
        configured: { type: boolean }
        enabled: { type: boolean }

    ConnectorsOut:
      type: object
      required: [slack]
      properties:
        slack: { $ref: '#/components/schemas/ConnectorStatusOut' }

    ConnectorToggleUpdate:
      type: object
      required: [enabled]
      properties:
        enabled: { type: boolean }
```

Also update `ErrorCode`'s enum list to include the new code:

```yaml
    ErrorCode:
      type: string
      description: Machine-readable failure code; the human explanation is in `detail`.
      enum: [booking_not_found, trip_not_found, flight_not_found, booking_expired, invalid_transition, validation_error, rate_limit_exceeded, connector_not_configured]
```

- [ ] **Step 3: Add the three new paths**

In `backend/specs/openapi.yaml`, under `paths`, add (after `/api/bookings/{log_id}/cancel`):

```yaml
  /api/slack/interactions:
    post:
      operationId: slack_interactions
      summary: >-
        Slack's interactivity callback for the booking-approval message. Verifies the request
        signature, then approves (confirm_booking) or rejects (cancel_booking) the booking named
        by the clicked button's value. Never executes a booking from this path.
      responses:
        '200':
          description: >-
            The interaction was processed (or was a recognized-but-unactionable payload) —
            the response body replaces the original Slack message.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/SlackInteractionOut' }
        '401':
          description: Missing/invalid Slack signature, or Slack is not configured.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/SlackAuthErrorOut' }

  /api/connectors:
    get:
      operationId: get_connectors
      summary: The configured/enabled status of each optional connector (currently just Slack).
      responses:
        '200':
          description: Current connector status.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/ConnectorsOut' }

  /api/connectors/slack:
    patch:
      operationId: set_slack_connector
      summary: Enable or disable the Slack connector. 409 if Slack env vars aren't configured.
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/ConnectorToggleUpdate' }
      responses:
        '200':
          description: Updated connector status.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/ConnectorsOut' }
        '409':
          description: Slack is not configured on this deployment (missing env vars).
          content:
            application/json:
              schema: { $ref: '#/components/schemas/ProblemDetail' }
```

- [ ] **Step 4: Confirm the contract test goes red (expected — routes don't exist yet)**

Run: `cd backend && uv run pytest tests/test_openapi_contract.py -q`
Expected: FAIL — `contract path /api/slack/interactions is not implemented at runtime` (and
similarly for the connectors paths). This is the intended "red" checkpoint of the contract-first
workflow; Tasks 4 and 5 turn it green.

- [ ] **Step 5: Commit**

```bash
git add backend/specs/openapi.yaml backend/app/schemas.py frontend/src/api/types.ts
git commit -m "docs: add OpenAPI contract for Slack interactions and connectors routes"
```

---

## Task 3: `app/adapters/slack_hitl.py` — core logic

**Files:**
- Create: `backend/app/adapters/slack_hitl.py`
- Create: `backend/tests/test_slack_hitl.py`

**Interfaces:**
- Consumes: `app.repositories.booking_repository.confirm_booking`, `.cancel_booking`,
  `.BookingError` (signatures: `async def confirm_booking(session: AsyncSession, log_id: int) -> HITLBookingLog`, same for `cancel_booking`; `BookingError.detail: str`). `app.models.TripRequest`, `FlightSearchResult`, `HITLBookingLog`. `app.config.SLACK_API_TIMEOUT_SECONDS`.
- Produces (used by Task 4 and Task 6):
  - `build_approval_blocks(trip: TripRequest, flight: FlightSearchResult, booking: HITLBookingLog) -> dict[str, Any]`
  - `verify_slack_signature(raw_body: bytes, timestamp: str, signature: str, signing_secret: str, *, now: float | None = None) -> bool`
  - `parse_block_action(payload: dict[str, Any], expected_channel_id: str) -> tuple[str, int] | None`
  - `resolve_approve(session: AsyncSession, booking_log_id: int) -> str`
  - `resolve_reject(session: AsyncSession, booking_log_id: int) -> str`
  - `build_resolution_blocks(outcome_text: str) -> dict[str, Any]`
  - `notify_pending_approval(settings: Settings, booking: HITLBookingLog, trip: TripRequest, flight: FlightSearchResult) -> None`

- [ ] **Step 1: Write the failing tests for signature verification**

Create `backend/tests/test_slack_hitl.py`:

```python
"""Slack HITL connector: signature verification, payload parsing, approve/reject resolution,
and the outbound notification — each tested at the level that would actually catch a regression,
not duplicated across unit and route layers (see test_slack_interactions_route.py for the
end-to-end signed-request path).
"""

import httpx
import pytest
from app.adapters.slack_hitl import (
    build_approval_blocks,
    build_resolution_blocks,
    notify_pending_approval,
    parse_block_action,
    resolve_approve,
    resolve_reject,
    verify_slack_signature,
)
from app.config import Settings
from app.models import FlightSearchResult, HITLBookingLog, TripRequest
from app.state import BookingState
from tests.db_helpers import get_booking, run_db, seed_booking

# Slack's own documented example (https://docs.slack.dev/authentication/verifying-requests-from-slack/).
_SLACK_DOC_SIGNING_SECRET = "8f742231b10e8888abcd99yyyzzz85a"
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.slack_hitl'`.

- [ ] **Step 3: Implement signature verification**

Create `backend/app/adapters/slack_hitl.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: 3 passed.

- [ ] **Step 5: Write the failing tests for `resolve_approve` / `resolve_reject`**

Append to `backend/tests/test_slack_hitl.py`:

```python
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
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: FAIL — `resolve_approve`/`resolve_reject` not defined.

- [ ] **Step 7: Implement `resolve_approve` / `resolve_reject`**

Append to `backend/app/adapters/slack_hitl.py`:

```python
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
    return "Rejected — the fare hold has been released."
```

- [ ] **Step 8: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: 6 passed.

- [ ] **Step 9: Write the failing test for the approval-card copy**

Append to `backend/tests/test_slack_hitl.py`:

```python
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
```

- [ ] **Step 10: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: FAIL — `build_approval_blocks` not defined.

- [ ] **Step 11: Implement `build_approval_blocks` and `build_resolution_blocks`**

Append to `backend/app/adapters/slack_hitl.py`:

```python
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
                        "text": f"*Price hold expires:*\n{booking.expires_at.isoformat()}",
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
                            "Approve to confirm this fare, or reject to release the hold. "
                            "Approving does not purchase the flight — you'll continue in "
                            "the app to complete it with the airline."
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
```

- [ ] **Step 12: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: 8 passed.

- [ ] **Step 13: Write the failing tests for `parse_block_action`**

Append to `backend/tests/test_slack_hitl.py`:

```python
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
    payload = _block_actions_payload(channel_id="C123", action_id="approve_booking", value="not-a-number")
    assert parse_block_action(payload, expected_channel_id="C123") is None
```

- [ ] **Step 14: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: FAIL — `parse_block_action` not defined.

- [ ] **Step 15: Implement `parse_block_action`**

Append to `backend/app/adapters/slack_hitl.py`:

```python
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
```

- [ ] **Step 16: Run to verify they pass**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: 11 passed.

- [ ] **Step 17: Write the failing test for `notify_pending_approval`'s tolerance**

Append to `backend/tests/test_slack_hitl.py`:

```python
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

    import asyncio

    asyncio.run(notify_pending_approval(settings, booking, trip, flight))
```

- [ ] **Step 18: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: FAIL — `notify_pending_approval` not defined.

- [ ] **Step 19: Implement `notify_pending_approval`**

Append to `backend/app/adapters/slack_hitl.py`:

```python
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
```

- [ ] **Step 20: Run the full test file, then the full suite**

Run: `cd backend && uv run pytest tests/test_slack_hitl.py -q`
Expected: 12 passed.

Run: `cd backend && uv run pytest -q`
Expected: all passing (existing suite untouched).

Run: `cd backend && uv run pyrefly check`
Expected: 0 errors.

- [ ] **Step 21: Commit**

```bash
git add backend/app/adapters/slack_hitl.py backend/tests/test_slack_hitl.py
git commit -m "feat: add Slack HITL adapter (signature verify, approve/reject resolution, card builder)"
```

---

## Task 4: `POST /api/slack/interactions` route

**Files:**
- Create: `backend/app/routes/slack.py`
- Modify: `backend/app/main.py`
- Modify: `backend/features/booking_hitl.feature`
- Modify: `backend/tests/steps/test_booking_hitl.py`

**Interfaces:**
- Consumes: everything produced in Task 3 (`verify_slack_signature`, `parse_block_action`, `resolve_approve`, `resolve_reject`, `build_resolution_blocks`), `app.db.get_session_factory`, `app.config.get_settings`.
- Produces: `POST /api/slack/interactions` mounted at that exact path in `app.main.app`.

- [ ] **Step 1: Write the Gherkin scenarios**

Append to `backend/features/booking_hitl.feature`:

```gherkin
  Scenario: A signed Slack approval confirms the booking
    Given a booking still pending user confirmation
    And Slack is configured with a known signing secret and channel
    When a correctly signed Slack approval for that booking arrives
    Then the Slack response is 200 with replace_original true
    And the booking ends CONFIRMED

  Scenario: An unsigned Slack request is rejected without touching the booking
    Given a booking still pending user confirmation
    And Slack is configured with a known signing secret and channel
    When an incorrectly signed Slack approval for that booking arrives
    Then the Slack response is 401
    And the booking is still PENDING_USER_CONFIRMATION
```

- [ ] **Step 2: Write the step definitions**

Append to `backend/tests/steps/test_booking_hitl.py` (add these imports at the top alongside the
existing ones, and the new step functions at the end of the file):

```python
import hashlib
import hmac
import time
```

```python
_TEST_SIGNING_SECRET = "test-signing-secret"
_TEST_CHANNEL_ID = "C_TEST_CHANNEL"


def _sign(body: bytes, timestamp: str) -> str:
    base_string = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(_TEST_SIGNING_SECRET.encode(), base_string, hashlib.sha256).hexdigest()
    return f"v0={digest}"


@given("Slack is configured with a known signing secret and channel")
def _slack_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config

    settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
        slack_bot_token="xoxb-test",
        slack_signing_secret=_TEST_SIGNING_SECRET,
        slack_approvals_channel_id=_TEST_CHANNEL_ID,
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr("app.routes.slack.get_settings", lambda: settings)


def _approval_form_body(log_id: int) -> bytes:
    import json
    import urllib.parse

    payload = json.dumps(
        {
            "type": "block_actions",
            "channel": {"id": _TEST_CHANNEL_ID},
            "actions": [{"action_id": "approve_booking", "value": str(log_id)}],
        }
    )
    return urllib.parse.urlencode({"payload": payload}).encode()


@when("a correctly signed Slack approval for that booking arrives")
def _signed_approval(client, log_id: int, bag: dict) -> None:
    body = _approval_form_body(log_id)
    timestamp = str(int(time.time()))
    bag["response"] = client.post(
        "/api/slack/interactions",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": _sign(body, timestamp),
        },
    )


@when("an incorrectly signed Slack approval for that booking arrives")
def _unsigned_approval(client, log_id: int, bag: dict) -> None:
    body = _approval_form_body(log_id)
    timestamp = str(int(time.time()))
    bag["response"] = client.post(
        "/api/slack/interactions",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": "v0=0000000000000000000000000000000000000000000000000000000000000000",
        },
    )


@then(parsers.parse("the Slack response is {status:d} with replace_original true"))
def _slack_response_replace_original(bag: dict, status: int) -> None:
    response = bag["response"]
    assert response.status_code == status, (
        f"expected {status}, got {response.status_code}: {response.text}"
    )
    assert response.json()["replace_original"] is True


@then(parsers.parse("the Slack response is {status:d}"))
def _slack_response_status(bag: dict, status: int) -> None:
    response = bag["response"]
    assert response.status_code == status, (
        f"expected {status}, got {response.status_code}: {response.text}"
    )


@then("the booking is still PENDING_USER_CONFIRMATION")
def _still_pending(log_id: int) -> None:
    booking = run_db(lambda session: get_booking(session, log_id))
    assert booking.state is BookingState.PENDING_USER_CONFIRMATION, (
        f"a rejected (unsigned) Slack interaction must not have touched the booking, "
        f"found state={booking.state}"
    )
```

- [ ] **Step 3: Run to verify the scenarios fail**

Run: `cd backend && uv run pytest tests/steps/test_booking_hitl.py -q`
Expected: FAIL — `404 Not Found` for `POST /api/slack/interactions` (route doesn't exist yet).

- [ ] **Step 4: Implement the route**

Create `backend/app/routes/slack.py`:

```python
"""Slack interactivity callback for the booking-approval message. Verifies Slack's webhook
signature before doing anything else, then approves or rejects the booking named by the
clicked button. Never executes a booking here — see docs/superpowers/specs for why.
"""

import json
from typing import Any

from app.adapters.slack_hitl import (
    build_resolution_blocks,
    parse_block_action,
    resolve_approve,
    resolve_reject,
    verify_slack_signature,
)
from app.config import get_settings
from app.db import get_session_factory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.schemas import SlackAuthErrorOut, SlackInteractionOut

router = APIRouter(prefix="/api/slack", tags=["slack"])

_UNCONFIGURED_OR_UNSIGNED = JSONResponse(
    status_code=401, content={"detail": "Slack is not configured or the signature is invalid."}
)


@router.post(
    "/interactions",
    response_model=SlackInteractionOut,
    responses={401: {"model": SlackAuthErrorOut}},
)
async def slack_interactions(request: Request) -> JSONResponse:
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
        return JSONResponse(content=build_resolution_blocks("Couldn't process that action."))
    try:
        payload: dict[str, Any] = json.loads(payload_raw)
    except json.JSONDecodeError:
        return JSONResponse(content=build_resolution_blocks("Couldn't process that action."))

    parsed = parse_block_action(payload, expected_channel_id=settings.slack_approvals_channel_id)
    if parsed is None:
        return JSONResponse(content=build_resolution_blocks("Couldn't process that action."))
    action_id, booking_log_id = parsed

    async with get_session_factory()() as session:
        if action_id == "approve_booking":
            outcome = await resolve_approve(session, booking_log_id)
        else:
            outcome = await resolve_reject(session, booking_log_id)

    return JSONResponse(content=build_resolution_blocks(outcome))
```

Add the two new response schemas to `backend/app/schemas.py` (after `AgentRunOut`, or anywhere
alongside the other `*Out` models):

```python
class SlackInteractionOut(BaseModel):
    replace_original: bool
    text: str
    blocks: list[dict[str, Any]]


class SlackAuthErrorOut(BaseModel):
    detail: str
```

- [ ] **Step 5: Wire the router into `main.py`**

In `backend/app/main.py`, add the import alongside the existing route imports:

```python
from app.routes import booking, slack, trips
```

And the include, alongside the other two:

```python
    app.include_router(booking.router)
    app.include_router(slack.router)
    app.include_router(trips.router)
```

- [ ] **Step 6: Run to verify the scenarios pass**

Run: `cd backend && uv run pytest tests/steps/test_booking_hitl.py -q`
Expected: all scenarios pass, including the two new ones.

- [ ] **Step 7: Run the contract test — the Slack path should now be green**

Run: `cd backend && uv run pytest tests/test_openapi_contract.py -q`
Expected: still FAIL, but only on the `/api/connectors*` paths now (Task 5 fixes those) — the
`/api/slack/interactions` failure from Task 2 Step 4 is gone.

- [ ] **Step 8: Run the full backend suite and Pyrefly**

Run: `cd backend && uv run pytest -q && uv run pyrefly check`
Expected: all tests pass except the still-expected `/api/connectors*` contract failures; 0 Pyrefly errors.

- [ ] **Step 9: Commit**

```bash
git add backend/app/routes/slack.py backend/app/main.py backend/app/schemas.py backend/features/booking_hitl.feature backend/tests/steps/test_booking_hitl.py
git commit -m "feat: add POST /api/slack/interactions route"
```

---

## Task 5: `/api/connectors` routes

**Files:**
- Create: `backend/app/routes/connectors.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_connectors_routes.py`

**Interfaces:**
- Consumes: `app.models.ConnectorSetting`, `app.config.get_settings`, `app.db.get_session` (the
  normal FastAPI-injected session — these are plain request/response routes, not out-of-request
  like the Slack webhook).
- Produces: `GET /api/connectors`, `PATCH /api/connectors/slack`, both mounted in `app.main.app`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_connectors_routes.py`:

```python
"""Connectors routes: the live, no-restart toggle for the Slack HITL connector. Only two
behaviors are worth a dedicated test here — the toggle actually persists (the entire reason
this is a DB row instead of an env var), and it can't be enabled without credentials (the
guard that stops a demo from silently no-oping)."""

import pytest


def test_patch_slack_connector_persists_across_get(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config

    settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
        slack_bot_token="xoxb-test", slack_signing_secret="secret",
        slack_approvals_channel_id="C123",
    )
    monkeypatch.setattr("app.routes.connectors.get_settings", lambda: settings)

    enable_response = client.patch("/api/connectors/slack", json={"enabled": True})
    assert enable_response.status_code == 200, enable_response.text
    assert enable_response.json()["slack"] == {"configured": True, "enabled": True}

    get_response = client.get("/api/connectors")
    assert get_response.json()["slack"] == {"configured": True, "enabled": True}, (
        "the toggle must survive a separate GET, not just echo back the PATCH body — that's "
        "the entire point of persisting it in connector_setting instead of in-memory"
    )


def test_patch_slack_connector_without_credentials_is_409(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forces the unconfigured state explicitly rather than relying on the ambient environment
    lacking Slack env vars — a developer who follows docs/SLACK_SETUP.md locally would otherwise
    silently flip this test from 409 to 200 the moment their own .env picks up real credentials."""
    from app import config

    unconfigured_settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
    )
    monkeypatch.setattr("app.routes.connectors.get_settings", lambda: unconfigured_settings)

    response = client.patch("/api/connectors/slack", json={"enabled": True})

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "connector_not_configured"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_connectors_routes.py -q`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Implement the routes**

Create `backend/app/routes/connectors.py`:

```python
"""Connectors routes: a live, DB-backed toggle for the Slack HITL connector — no separate
repository module, this is a single row with two simple queries."""

from app.config import Settings, get_settings
from app.db import get_session
from app.models import ConnectorSetting
from app.schemas import ConnectorsOut, ConnectorStatusOut, ConnectorToggleUpdate, ErrorCode
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorError(Exception):
    def __init__(self, code: ErrorCode, status_code: int, detail: str) -> None:
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _slack_configured(settings: Settings) -> bool:
    return bool(
        settings.slack_bot_token
        and settings.slack_signing_secret
        and settings.slack_approvals_channel_id
    )


async def _get_or_create_row(session: AsyncSession) -> ConnectorSetting:
    row = await session.scalar(select(ConnectorSetting))
    if row is None:
        row = ConnectorSetting()
        session.add(row)
        await session.commit()
    return row


@router.get("", response_model=ConnectorsOut)
async def get_connectors(session: AsyncSession = Depends(get_session)) -> ConnectorsOut:
    settings = get_settings()
    row = await _get_or_create_row(session)
    return ConnectorsOut(
        slack=ConnectorStatusOut(configured=_slack_configured(settings), enabled=row.slack_enabled)
    )


@router.patch("/slack", response_model=ConnectorsOut)
async def set_slack_connector(
    body: ConnectorToggleUpdate, session: AsyncSession = Depends(get_session)
) -> ConnectorsOut:
    settings = get_settings()
    if body.enabled and not _slack_configured(settings):
        raise ConnectorError(
            ErrorCode.CONNECTOR_NOT_CONFIGURED,
            409,
            "Slack is not configured on this deployment (missing bot token, signing secret, "
            "or channel id).",
        )
    row = await _get_or_create_row(session)
    row.slack_enabled = body.enabled
    session.add(row)
    await session.commit()
    return ConnectorsOut(
        slack=ConnectorStatusOut(configured=_slack_configured(settings), enabled=row.slack_enabled)
    )
```

Add the request/response schemas to `backend/app/schemas.py`:

```python
class ConnectorStatusOut(BaseModel):
    configured: bool
    enabled: bool


class ConnectorsOut(BaseModel):
    slack: ConnectorStatusOut


class ConnectorToggleUpdate(BaseModel):
    enabled: bool
```

- [ ] **Step 4: Wire the router and its exception handler into `main.py`**

In `backend/app/main.py`, update the import:

```python
from app.routes import booking, connectors, slack, trips
from app.routes.connectors import ConnectorError
```

Add the include:

```python
    app.include_router(booking.router)
    app.include_router(connectors.router)
    app.include_router(slack.router)
    app.include_router(trips.router)
```

Add the exception handler, alongside the existing `BookingError`/`TripError` ones:

```python
    @app.exception_handler(ConnectorError)
    async def _render_connector_error(request: Request, error: ConnectorError) -> JSONResponse:
        problem = ProblemDetail(code=error.code, detail=error.detail)
        return JSONResponse(status_code=error.status_code, content=problem.model_dump(mode="json"))
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `cd backend && uv run pytest tests/test_connectors_routes.py -q`
Expected: 2 passed.

- [ ] **Step 6: Run the contract test — should now be fully green**

Run: `cd backend && uv run pytest tests/test_openapi_contract.py -q`
Expected: 3 passed (all contract tests, no remaining path/schema drift).

- [ ] **Step 7: Run the full backend suite and Pyrefly**

Run: `cd backend && uv run pytest -q && uv run pyrefly check`
Expected: all passing, 0 errors.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/connectors.py backend/app/main.py backend/app/schemas.py backend/tests/test_connectors_routes.py
git commit -m "feat: add GET/PATCH /api/connectors routes"
```

---

## Task 6: Wire the notification into `request_booking`

**Files:**
- Modify: `backend/app/routes/booking.py`
- Modify: `backend/tests/test_trip_routes.py` (or a new small test file — see Step 1)

**Interfaces:**
- Consumes: `app.adapters.slack_hitl.notify_pending_approval`, `app.models.ConnectorSetting`,
  `app.models.TripRequest`, `app.models.FlightSearchResult`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_slack_notification_wiring.py`:

```python
"""Proves the one thing the Connectors toggle exists for: flipping it off must provably stop
the outbound Slack notification, not just theoretically stop it."""

from unittest.mock import AsyncMock

import pytest
from tests.db_helpers import run_db, seed_trip, seed_flight_search_results


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_slack_notification_wiring.py -q`
Expected: FAIL — the enabled case asserts a call that never happens (wiring doesn't exist yet);
the disabled case passes trivially today, which is fine, it'll stay meaningful once the wiring exists.

- [ ] **Step 3: Wire the call into the route**

In `backend/app/routes/booking.py`, the current import block (top of file) reads:

```python
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dbos_runtime import execute_booking_durable
from app.models import BookingTransition, HITLBookingLog
from app.repositories import booking_repository as repository
from app.schemas import (
    BookingLogOut,
    BookingRequestCreate,
    BookingTransitionOut,
    ProblemDetail,
)
```

Replace it with:

```python
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.slack_hitl import notify_pending_approval
from app.config import get_settings
from app.db import get_session
from app.dbos_runtime import execute_booking_durable
from app.models import (
    BookingTransition,
    ConnectorSetting,
    FlightSearchResult,
    HITLBookingLog,
    TripRequest,
)
from app.repositories import booking_repository as repository
from app.schemas import (
    BookingLogOut,
    BookingRequestCreate,
    BookingTransitionOut,
    ProblemDetail,
)
```

Replace the `request_booking` handler body:

```python
@router.post(
    "/trips/{trip_id}/booking/request", response_model=BookingLogOut, responses=_NOT_FOUND
)
async def request_booking(
    trip_id: int,
    body: BookingRequestCreate,
    session: AsyncSession = Depends(get_session),
) -> BookingLogOut:
    booking = await repository.request_booking(session, trip_id, body.flight_search_result_id)
    await _notify_slack_if_enabled(session, booking)
    return _to_out(booking)


async def _notify_slack_if_enabled(session: AsyncSession, booking: HITLBookingLog) -> None:
    settings = get_settings()
    if not (
        settings.slack_bot_token
        and settings.slack_signing_secret
        and settings.slack_approvals_channel_id
    ):
        return
    connector_row = await session.scalar(select(ConnectorSetting))
    if connector_row is None or not connector_row.slack_enabled:
        return
    trip = await session.get(TripRequest, booking.trip_request_id)
    flight = await session.get(FlightSearchResult, booking.flight_search_result_id)
    assert trip is not None and flight is not None, (
        "request_booking already validated these exist"
    )
    await notify_pending_approval(settings, booking, trip, flight)
```

Add `from sqlalchemy import select` to the imports if not already present in this file (it
isn't — `booking.py` currently only imports `AsyncSession`, `APIRouter`, `Depends`).

- [ ] **Step 4: Run to verify the tests pass**

Run: `cd backend && uv run pytest tests/test_slack_notification_wiring.py -q`
Expected: 2 passed.

- [ ] **Step 5: Run the full backend suite and Pyrefly**

Run: `cd backend && uv run pytest -q && uv run pyrefly check`
Expected: all passing, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/booking.py backend/tests/test_slack_notification_wiring.py
git commit -m "feat: notify Slack on booking request when the connector is configured and enabled"
```

---

## Task 7: Frontend types and API client

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces: `ConnectorStatusOut`, `ConnectorsOut` types; `getConnectors()`,
  `setSlackConnectorEnabled(enabled: boolean)` client functions.

- [ ] **Step 1: Add the types**

Append to `frontend/src/api/types.ts`:

```typescript
export interface ConnectorStatusOut {
  configured: boolean
  enabled: boolean
}

export interface ConnectorsOut {
  slack: ConnectorStatusOut
}
```

- [ ] **Step 2: Add the client functions**

In `frontend/src/api/client.ts`, add `ConnectorsOut` to the existing `import type` block, then
append at the end of the file:

```typescript
export function getConnectors(): Promise<ConnectorsOut> {
  return request<ConnectorsOut>("/connectors")
}

export function setSlackConnectorEnabled(enabled: boolean): Promise<ConnectorsOut> {
  return request<ConnectorsOut>("/connectors/slack", {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  })
}
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: builds cleanly (this step only adds types/functions, nothing calls them yet — verifies
no syntax/type errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "feat: add Connectors API types and client functions"
```

---

## Task 8: Frontend Connectors tab

**Files:**
- Create: `frontend/src/components/ConnectorsPanel.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `getConnectors`, `setSlackConnectorEnabled` (Task 7), `ConnectorsOut` type.

- [ ] **Step 1: Create the panel component**

Create `frontend/src/components/ConnectorsPanel.tsx`:

```tsx
import { useEffect, useState } from "react"
import { ApiError, getConnectors, setSlackConnectorEnabled } from "../api/client"
import type { ConnectorsOut } from "../api/types"

export function ConnectorsPanel() {
  const [connectors, setConnectors] = useState<ConnectorsOut | null>(null)
  const [isToggling, setIsToggling] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    getConnectors()
      .then(setConnectors)
      .catch(() => setErrorMessage("Could not load connector status."))
  }, [])

  const handleToggle = async () => {
    if (!connectors) return
    const nextEnabled = !connectors.slack.enabled
    setIsToggling(true)
    setErrorMessage(null)
    try {
      setConnectors(await setSlackConnectorEnabled(nextEnabled))
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Could not update the connector.")
    } finally {
      setIsToggling(false)
    }
  }

  if (!connectors) {
    return (
      <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
        Loading connectors…
      </p>
    )
  }

  const { configured, enabled } = connectors.slack

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Connectors</h2>
        <p className="mt-1 text-sm text-slate-500">
          Optional delivery channels for the booking approval gate — the underlying state
          machine is unchanged either way.
        </p>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-slate-200 p-4">
        <div>
          <p className="font-medium text-slate-900">Slack</p>
          <p className="mt-1 text-sm text-slate-500">
            Post a Confirm/Reject message to Slack when a booking needs approval.
          </p>
          {!configured && (
            <p className="mt-1 text-sm text-amber-600">
              Slack credentials not configured on the server.
            </p>
          )}
          {errorMessage && <p className="mt-1 text-sm text-red-600">{errorMessage}</p>}
        </div>
        <button
          type="button"
          onClick={handleToggle}
          disabled={!configured || isToggling}
          className={`min-h-11 rounded-lg border px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
            enabled
              ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
              : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
          }`}
        >
          {enabled ? "Enabled" : "Disabled"}
        </button>
      </div>
    </section>
  )
}
```

- [ ] **Step 2: Wire the tab into `App.tsx`**

In `frontend/src/App.tsx:10-16`, add the import alongside the other component imports:

```typescript
import { ConnectorsPanel } from "./components/ConnectorsPanel"
```

Update the tab type and list (`App.tsx:24-29`):

```typescript
type TabKey = "trip" | "execution" | "connectors"

const TABS: { key: TabKey; label: string }[] = [
  { key: "trip", label: "Plan a trip" },
  { key: "execution", label: "Agent execution history" },
  { key: "connectors", label: "Connectors" },
]
```

Add the panel render, after the `{activeTab === "execution" && ...}` block (`App.tsx`, inside
`<main>`, right before `</main>`):

```tsx
          {activeTab === "connectors" && <ConnectorsPanel />}
```

- [ ] **Step 3: Verify the build and lint**

Run: `cd frontend && npm run build`
Expected: builds cleanly.

Run: `cd frontend && npm run lint`
Expected: only the pre-existing `App.tsx` `exhaustive-deps` warning, no new warnings/errors.

- [ ] **Step 4: Manual smoke test**

Run: `cd backend && uv run uvicorn app.main:app --reload` (one terminal) and
`cd frontend && npm run dev` (another terminal). Open the app, click the "Connectors" tab,
confirm:
- Without `SLACK_BOT_TOKEN`/`SLACK_SIGNING_SECRET`/`SLACK_APPROVALS_CHANNEL_ID` set, the Slack
  row shows "Disabled", the button is greyed out/disabled, and "Slack credentials not
  configured on the server" is visible.
- With those three env vars set (dummy values are fine for this check — the button should
  still toggle even though `notify_pending_approval` would fail against a fake token later),
  the button becomes clickable and toggling it flips between "Enabled"/"Disabled",
  persisting across a page reload.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConnectorsPanel.tsx frontend/src/App.tsx
git commit -m "feat: add Connectors tab with live Slack toggle"
```

---

## Task 9: Slack app setup docs

**Files:**
- Create: `docs/SLACK_SETUP.md`

- [ ] **Step 1: Write the setup doc**

Create `docs/SLACK_SETUP.md`:

```markdown
# Slack HITL connector — setup

One-time steps to enable the optional Slack approval connector. Nothing here is required for
the app to run — without these env vars, the Connectors tab shows the Slack toggle greyed out
and the app behaves exactly as it does today.

1. Create an app at https://api.slack.com/apps in your workspace.
2. **OAuth & Permissions** → Bot Token Scopes → add `chat:write`. Click **Install to
   Workspace**, then copy the **Bot User OAuth Token** (`xoxb-...`).
3. **Basic Information** → copy the **Signing Secret**.
4. Create (or choose) the channel approvals should post to, invite the bot
   (`/invite @YourBotName`), then open the channel details and copy its **Channel ID**
   (`C...`).
5. **Interactivity & Shortcuts** → toggle on → set **Request URL** to
   `https://<your-tunnel>/api/slack/interactions`.
6. For local development, expose your backend with `ngrok http 8000` and use the printed
   `https://*.ngrok.io` URL as `<your-tunnel>` above.
7. Set these three variables in `backend/.env` (see `.env.example`):
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_SIGNING_SECRET=...
   SLACK_APPROVALS_CHANNEL_ID=C...
   ```
8. Restart the backend, open the app's **Connectors** tab, and click the Slack toggle to
   **Enabled**.
9. Request a booking in the app — the approval message should appear in the configured
   channel within a few seconds.
```

- [ ] **Step 2: Commit**

```bash
git add docs/SLACK_SETUP.md
git commit -m "docs: add Slack HITL connector setup guide"
```
