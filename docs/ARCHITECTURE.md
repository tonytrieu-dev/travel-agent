# Architecture

## System overview

```mermaid
flowchart LR
    UI[React frontend] -->|REST /api| API[FastAPI]
    API --> Repo[Repositories]
    Repo --> DB[(Postgres 16)]
    API --> Agent[Pydantic AI agent]
    Agent -->|search_flights| Flights[FlightProvider\nLive SearchApi / Recorded cassette]
    Agent -->|web_search| Tavily[TavilyActivityProvider]
    Flights --> SearchApi[(SearchApi.io)]
    Tavily --> TavilyAPI[(Tavily API)]
    Agent --> Cerebras[(Cerebras gpt-oss-120b)]
    Agent -.->|run_planner_durable| DBOS[DBOS workflow]
    Repo -.->|execute_booking_durable| DBOS
    DBOS --> DB
    API -->|/api/connectors| ConnSetting[(connector_setting)]
    API -->|notify_pending_approval| Slack[(Slack)]
    Slack -->|/api/slack/interactions| API
```

The backend is a single FastAPI process. The frontend is a separate static SPA that talks to it
over REST; there is no server-rendered coupling between them.

## Requirements → implementation

- **Cheapest flights:** `FlightSearchService` (`app/services/flight_search.py`) — the one
  implementation behind both `POST /flights/search` and the planner's `search_flights` tool —
  sorts every offer ascending by `price_usd` via `trips_repository.py::cheapest_first` (shared,
  not duplicated) on every path: fresh search, own-trip TTL reuse, cross-trip cache. A backend
  guarantee, not just provider ordering; the UI lists offers in that order, so the cheapest is
  always shown first. The two callers differ only in `persist`/`allow_cross_trip_cache`: the route
  persists and reaches across trips, the planner tool trusts only this trip's own recent search
  and never writes offers (see "The agent has only two read-only tools" in `DECISIONS.md`).
- **Itinerary from a real API, tailored to age/fitness:** `web_search` (Tavily) grounds every
  activity in a real, cited source; `output_type=[ItineraryOut, ClarificationOut]` plus the
  `reject_unsafe_intensity` guardrail tie activity intensity to the traveler's fitness level.
- **Ask, don't assume:** genuinely ambiguous inputs (not missing ones — those are required at
  intake) produce a `ClarificationOut` instead of a guessed itinerary.
- **Visible UI:** React SPA with a live tool-call feed and an execution panel (see below).
- **HITL booking (bonus):** explicit confirm-then-execute clicks gate the only booking write;
  see "HITL booking" below for what "execute" does and doesn't do.
- **Slack HITL connector (bonus):** an optional, DB-toggled Slack approval message with
  Confirm/Reject buttons offers the same gate through Slack instead of the UI; see "Slack HITL
  connector" below.

## Architectural patterns

Five patterns are enforced explicitly, each chosen over a simpler alternative for a reason named
in [DECISIONS.md](DECISIONS.md) — this section is the map from pattern name to where it lives in
code; DECISIONS.md has the "why this, not X" reasoning for each.

1. **Dependency Injection** (FastAPI `Depends`) — DB sessions (`get_session`) and external clients
   (flight provider, booking-options fetcher) are injected into route handlers, never constructed
   inside them. `agent/planner.py`'s `PlannerDeps` extends the same idea into the agent: the
   planner takes its `FlightProvider`/`ActivityProvider` as constructor args, so a test can hand it
   a fake without touching the model call.
2. **Finite State Machine** (`app/state.py`) — `ALLOWED_TRANSITIONS` is the single source of truth
   for booking state (`PENDING → CONFIRMED → EXECUTED`, +`CANCELLED`/`EXPIRED`); every transition
   is a guard clause (illegal move → 409) plus an append-only audit row in the same transaction.
   See "HITL booking" below and "HITL booking is a REST state machine, not an agent tool" in
   DECISIONS.md.
3. **Repository pattern** (`app/repositories/*`) — all Postgres access lives behind
   `trips_repository.py`/`booking_repository.py`; route handlers call the repository and shape a
   response, never build a query or own a transaction boundary themselves.
4. **Strategy pattern** (`FlightProvider` in `app/adapters/flights_searchapi.py`) — one `Protocol`,
   two interchangeable implementations (`LiveSearchApiProvider`, `RecordedProvider`) selected once
   at composition by `USE_LIVE_FLIGHT_API`. `FlightSearchService`, the route, and the planner tool
   all depend on the interface via DI and never branch on the toggle themselves.
5. **Durable execution, not Saga** (DBOS) — `execute_booking_durable` and the planner run are
   `@DBOS.workflow`s so a crash mid-run resumes instead of losing state. **Honest framing:** a
   single-DB booking write is already atomic (one ACID transaction + `SELECT ... FOR UPDATE`), so
   this is durable-execution for crash recovery, not classic Saga compensation — compensation
   (release a hold, refund a charge) only becomes load-bearing once a real airline booking is
   multi-step (hold → charge → confirm), which is out of this take-home's scope (see "Deferred by
   design" in DECISIONS.md). `execute_booking`'s structure — an isolated external step plus an
   isolated state transition — is deliberately shaped so adding real Saga compensation later would
   be additive, not a rewrite.

`FlightSearchService`/`ExecutionService` (see "Flight search and execution-run lifecycle are
extracted services" in DECISIONS.md) are a sixth, smaller pattern in the same family — extracting
duplicated logic behind one interface — but weren't part of the original five; they're a
refactor-era addition once the duplication became real, not a pattern picked up front.

## APIs & AI protocols

**External APIs** (all free tier):

| API | Role | Adapter |
|---|---|---|
| Cerebras (`gpt-oss-120b`) | The planner LLM — reasoning, tool selection, structured output. | Pydantic AI `CerebrasModel`/`CerebrasProvider` in `planner.py`. |
| SearchApi.io Google Flights | Real flight offers + booking options. | `flights_searchapi.py` (Live vs Recorded strategy). |
| Tavily | Real, source-attributed activity research. | `activities_tavily.py`. |
| Slack (optional) | Approval message with Confirm/Reject buttons; signed callback. | `slack_hitl.py` + `routes/slack.py`. |

**AI protocols:**

- **REST** between the React frontend and FastAPI backend.
- **LLM tool/function calling** — the model chooses when to call `search_flights` and
  `web_search`; results feed back into its context.
- **JSON Schema structured output** — Pydantic AI validates the model's output against
  `output_type=[ItineraryOut, ClarificationOut]`; "ask, don't assume" is a type, not a hope.
- **ReAct-style agent loop** — `agent.iter(...)` drives a reason → act (tool call) → observe
  cycle until the model resolves to a final structured output.

**Supporting engineering (not protocols, but load-bearing):**

- **Usage limits** — `UsageLimits(tool_calls_limit=MAX_TOOL_STEPS, total_tokens_limit=MAX_CONTEXT_TOKENS)`
  bounds the loop so it can't spin; `MAX_CONTEXT_TOKENS` matches gpt-oss-120b's real 30K
  tokens/minute limit on Cerebras. A run that exceeds it degrades to a clarifying question
  instead of crashing.
- **Prompt-injection guardrail** — `sanitize_web_content` wraps untrusted Tavily text in a delimited,
  escaped block before it reaches the prompt, so embedded instructions read as data.
- **Durable steps (DBOS)** — the planner run and booking execute are checkpointed workflows that
  resume after a crash.
- **Observability** — `AgentRun`/`AgentRunStep` rows are derived from the real message history and
  usage, powering the execution panel.
- **Eval scoring (`pydantic-evals`)** — deterministic evaluators by default, plus an opt-in
  `LLMJudge` for fitness-appropriateness behind `--with-judge`. See [EVALS.md](EVALS.md) for the
  exact-vs-subjective split and why.

## Request/agent flow

**Planning a trip** (`POST /api/trips/{id}/plan`): `plan_trip` is idempotent per trip
(`get_or_create_itinerary` returns an existing `Itinerary` row as-is). Otherwise it calls
`run_planner_durable` (`app/dbos_runtime.py`), which acquires a concurrency slot
(`acquire_agent_run_slot`, caps concurrent real LLM calls) and runs the `@DBOS.workflow`-wrapped
planner: `ExecutionService(session).start_run(...)` binds an `ExecutionRun` for the trip, then
`agent.iter(...)` drives a ReAct-style loop over `search_flights`/`web_search`, capped by
`MAX_TOOL_STEPS`/`MAX_CONTEXT_TOKENS`. The output resolves to the `ItineraryOut | ClarificationOut`
union — a `ClarificationOut` returns questions without persisting an itinerary; an `ItineraryOut`
persists and moves the trip to `ITINERARY_READY`. Every tool call records an `ExecutionEvent`
through the bound run, and `ExecutionRun.persist_result` (wrapping `persist_agent_run`) derives
`AgentRun`/`AgentRunStep` rows from the real message history and usage on both the success and
crash-recovery failure paths (never fabricated) — `ExecutionService`/`ExecutionRun`
(`app/agent/execution_log.py`) is the one place that finalizes a run, so there's exactly one
finalization path to reason about, not two. The concurrency slot releases in a `finally`, outside
the DBOS-wrapped call — see [DECISIONS.md](DECISIONS.md) for why that placement matters.
`POST /flights/search` (outside the agent loop) still binds its own run through the lower-level
`execution_context()` directly, since it isn't wrapped in a DBOS workflow.

**Booking a flight** (the HITL gate): a REST state machine, not an agent capability. See
"HITL booking" below.

**Watching a run**: `GET /api/trips/{id}/execution` reads every `AgentRun` with its owned
`AgentRunStep`s and `ExecutionEvent`s, shaped into `ExecutionPanelOut` with derived context usage
and estimated cost. The response also retains the trip-wide event stream for LiveActivity; the
execution panel renders events only inside their owning run. `GET /api/execution` is the same
shape across every trip the user owns (`GlobalExecutionPanelOut`) — it backs the "Agent execution
history" tab, which is global rather than scoped to whichever trip is currently active (see
"Trip history is a list endpoint plus a global execution feed" in `DECISIONS.md`). The tab filters
that list client-side by route text and run status; `GET /api/trips` (also newest-first,
user-scoped) backs the sibling "Your trips" tab, filtered client-side by a date-range dropdown.

## Data model

Five core tables (`user_account`, `trip_request`, `flight_search_result`, `itinerary`,
`hitl_booking_log`), one connector-config table (`connector_setting` — a single-row, DB-backed
toggle so the Slack connector can be flipped at runtime without a restart), and four
audit/observability tables (`booking_transition`, `execution_event`, `agent_run`, `agent_run_step`)
in `app/models.py`. `booking_transition` and `execution_event` are append-only, enforced by a
Postgres trigger (`reject_audit_row_mutation()`) — `UPDATE`/`DELETE` raises at the database level
regardless of what application code attempts.

## HITL booking (`app/state.py`)

![HITL booking state machine](assets/hitl-state-machine.png)

`ALLOWED_TRANSITIONS` is the single source of truth; any move not listed is rejected with a 409
and never reaches the database. `execute_booking` is the highest-value guard in the system: it
claims the row with `SELECT ... FOR UPDATE`, re-checks state under that lock, and fetches
booking options exactly once — a double-click from an impatient human can never trigger a second
`booking_options` fetch or burn a second unit of the flight-search quota (see
`test_double_execute_books_once`). This entire state machine lives outside the agent; the agent
can plan and search but has no tool that can move a booking's state, so "a human must click
confirm, then execute" is structural, not a prompt instruction the model could be talked out of.

**Scope note:** "execute" fetches real booking options from SearchApi and stamps an internal
`TA-*` reference on the `HITLBookingLog` row — it's a human-confirmed booking *handoff*, not a
real airline reservation/purchase (no PNR, no payment). Those options render as per-provider
checkout buttons the traveler clicks through to complete the purchase on the carrier's own site,
so no fare is ever held here; `BOOKING_TTL_MINUTES` is an internal price-freshness window, checked
lazily on confirm/execute rather than by any sweeper. Completing a real purchase is out of scope
for this take-home; see [DECISIONS.md](DECISIONS.md).

## Slack HITL connector (`app/adapters/slack_hitl.py`, `app/routes/slack.py`, `app/routes/connectors.py`)

Optional, off by default. `GET/PATCH /api/connectors` reads and flips the single-row
`connector_setting.slack_enabled` toggle, gated so it can only be enabled when
`SLACK_BOT_TOKEN`/`SLACK_SIGNING_SECRET`/`SLACK_APPROVALS_CHANNEL_ID` are all configured (409
otherwise). The frontend's Connectors tab (`ConnectorsPanel.tsx`) drives this toggle.

When enabled, `request_booking` (`routes/booking.py`) additionally posts a Confirm/Reject Block
Kit message via `notify_pending_approval`; Slack's callback hits `POST /api/slack/interactions`,
which verifies the request signature (stdlib `hmac`, constant-time compare) before resolving to
the same `confirm_booking`/`reject_booking` repository calls the in-app buttons use — Slack is an
alternate front door to the identical state machine, not a second one. Slack's Interactivity
config requires a public HTTPS URL for that callback, so local development tunnels the backend
with `ngrok http 8000`. See [SLACK_SETUP.md](SLACK_SETUP.md) for setup and
[DECISIONS.md](DECISIONS.md) for why this is a hand-rolled adapter instead of a third-party chat
SDK.

## Agent Execution Panel

Watch the agent work, live or after the fact: each run card combines metrics, model calls, tool
calls, the structured output, and its own API/protocol activity. The output is its own
`AgentStepKind.OUTPUT` rather than a tool call — pydantic-ai delivers a result by calling a
synthetic `final_result_<Type>` tool, so grouping it with `search_flights`/`web_search` made the
itinerary look like an agent tool invocation. A refused attempt is recorded `rejected`, which is
what a retry-exhausted run looks like. Backed entirely by real persisted data
(`agent_run`/`agent_run_step`/`execution_event`), not live in-memory state, so it reflects exactly
what happened — including runs from before the current process started.

## Durable execution (DBOS)

Two flows are wrapped as `@DBOS.workflow`s so a process crash mid-run resumes rather than
silently losing state: `execute_booking_durable` (`app/dbos_runtime.py`) and the planner run
(`_run_planner_workflow`). DBOS reuses the app's own Postgres instance (its own `dbos` schema) —
no additional infrastructure. Because DBOS workflows must take only serializable arguments and
may replay their body during crash recovery, both durable entry points rebuild their
session/provider dependencies internally rather than receiving them injected, and neither
mutates plain in-process state (locks, counters) from inside the workflow body — see
[DECISIONS.md](DECISIONS.md) for the concurrency-limiter bug this constraint caused and how it
was fixed.
