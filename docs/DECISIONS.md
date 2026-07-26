# Decisions

Load-bearing choices, each with the alternative and why it was rejected.

## Dependency Injection over constructing dependencies inline
DB sessions and external clients (`FlightProvider`, `ActivityProvider`, the booking-options
fetcher) are injected — via FastAPI `Depends` in routes, via constructor/dataclass args
(`PlannerDeps`) in the agent — never instantiated inside the function that uses them.
**Alternative:** construct them where needed (e.g. `LiveSearchApiProvider()` directly inside a
route or the planner tool). **Rejected** — that couples every call site to one concrete
implementation, so testing the booking state machine's concurrency (`test_double_execute_books_once`)
or the planner's tool-calling loop would require mocking internals instead of handing in a real
fake object that behaves like the thing it replaces.

## Repository pattern over inline queries in routes
All Postgres access lives in `app/repositories/*` (`trips_repository.py`, `booking_repository.py`);
route handlers call a repository function and shape the HTTP response, never build a query or own
a transaction boundary directly. **Alternative:** query the ORM directly from route handlers, the
FastAPI default. **Rejected** — the state machine's guard-clause-plus-audit-write pattern
(see below) has to be atomic and consistent everywhere a transition happens; centralizing DB access
in one layer is what makes "every transition writes an audit row in the same transaction" a
property of the repository, not something each route has to remember to do correctly on its own.

## HITL booking is a REST state machine, not an agent tool
The booking write moves through `PENDING → CONFIRMED → EXECUTED` (or `CANCELLED`/`EXPIRED`) via
explicit `/bookings/*` calls driven by human clicks. **Alternative:** expose booking as an agent
tool gated by an approval prompt. **Rejected** because a prompt-gated tool makes "a human
confirmed first" a prompt-dependent hope; a state machine outside the agent makes it structural —
the agent has no tool that can move booking state.

## Agent output is a union: `Itinerary | ClarificationOut`
A genuinely ambiguous input (e.g. a destination name that could mean more than one place) produces
real clarifying questions, not a guessed itinerary. **Alternative:** always return an itinerary and
let the prompt beg the model to ask. **Rejected** — "ask, don't assume" as a type is enforced by
validation; as prose it's optional. Age/fitness level used to be the main trigger for this path
until they became mandatory at trip intake (see the "mandatory intake fields" note below) — the
union stays for whatever's still genuinely ambiguous.

## Age and fitness level are mandatory intake fields
`TripRequestCreate.age`/`.fitness_level` are required, not optional-then-clarified. **Alternative:**
keep them optional and let the agent's `ClarificationOut` path ask when missing (the original
design). **Rejected** — every itinerary needs them to pace activities, so the clarify-then-resubmit
round trip was guaranteed on nearly every real trip; validating at intake removes that round trip
entirely instead of just making it reliable. `TripRequest.age`/`.fitness_level` are now `NOT NULL`
in the DB too (migration `12c1788c`) — a nullable column let a handful of legacy rows carry no
age/fitness, and `reject_optional_clarification` blocks any clarifying question that mentions
"age"/"fitness" once other trip details are present, so a genuinely-null row trapped the model
asking for the one thing it wasn't allowed to ask about, burning all 3 output retries. Closing the
nullability gap fixes that structurally instead of special-casing the validator. The 210 affected
legacy rows kept their append-only audit history and got a neutral placeholder backfilled; the 2
with no audit trail were dropped.

## Flight search and execution-run lifecycle are extracted services, not inline route/tool logic
`FlightSearchService` (`app/services/flight_search.py`) and `ExecutionService`/`ExecutionRun`
(`app/agent/execution_log.py`) sit behind `POST /flights/search`, the planner's `search_flights`
tool, and the DBOS-wrapped planner run. **Alternative:** leave the caching/persistence/ordering
logic inline in `routes/trips.py` and `agent/planner.py`, as it originally was. **Rejected** — the
route and the planner tool need the *same* cheapest-first/cache/round-trip-completeness behavior
(same-trip TTL reuse, cross-trip identical-search reuse, honest-unavailable degradation) and had
drifted into two near-duplicate implementations; one service parameterized by
`persist`/`allow_cross_trip_cache` is the single place that logic can be verified once
(`test_route_and_planner_tool_modes_agree_on_offer_ordering_and_shape` pins the two callers can't
silently diverge again). Same reasoning for `ExecutionService`: before the extraction, the DBOS
workflow and its failure-path cleanup had two separate ways to finalize an `AgentRun`
(`persist_agent_run` called directly from two branches); `ExecutionRun.persist_result` is now the
one path, so `_persist_failed_run` and the success path can't fall out of sync on what "finalized"
means. Both extractions were scoped to change no observable behavior — the full plan and
task-by-task TDD trail live in
`docs/superpowers/plans/2026-07-24-flight-search-execution-services.md`.

## The agent has only two read-only tools
Only `search_flights` and `web_search` are registered on the planner, both with strict JSON
schemas. Booking remains outside the agent as the REST state machine above, so the model has no
write tool to invoke.

## Audit tables are append-only at the database
`booking_transition` and `execution_event` have `BEFORE UPDATE/DELETE` triggers that raise.
**Alternative:** enforce immutability in application code. **Rejected** — app-level convention is
one bug away from a silent tamper; the DB trigger holds regardless of the code path.

## DBOS for durable execution, crash-recovery only
The planner run and booking execute are `@DBOS.workflow`s reusing the app's Postgres. Deliberately
**no** DBOS-level dedup on top of the existing `SELECT ... FOR UPDATE` claim — one mechanism, one
job (DBOS = crash recovery). **Alternative:** add `SetWorkflowID` dedup too. **Rejected** as
redundant with the tested atomic claim.

### The concurrency slot lives *outside* the DBOS workflow body
`run_planner_durable` acquires the concurrency slot, then calls the `@DBOS.workflow`. The slot is
plain in-process state (a lock-guarded counter). **Why outside:** DBOS's record/persist machinery
re-enters the workflow body during replay, so mutating in-process state *inside* it double-counts
(observed: one acquire showed as two). Keeping the slot in the plain outer function is the fix the
[ARCHITECTURE](ARCHITECTURE.md) durable-execution section refers to. Related: the non-blocking
acquire uses a lock-guarded counter, not `asyncio.wait_for(sem.acquire(), timeout=0)`, which can
spuriously time out even uncontended.

## Cerebras over Groq (over Gemini)
Cerebras runs `gpt-oss-120b` directly through Pydantic AI's native `CerebrasModel`/
`CerebrasProvider`. The model name lives in `config.py::CEREBRAS_MODEL`, and the app reads
`CEREBRAS_API_KEY` from settings. **Alternative 1:** Groq, also serving `gpt-oss-120b`.
**Rejected** — Groq's free tier caps at 8,000 tokens/minute, which crashed multi-tool-call planner
runs with HTTP 413 "request too large" rate-limit errors; Cerebras's free tier gives 30,000
tokens/minute for the same model, so itinerary generation completes end-to-end. **Alternative 2:**
`llama-3.3-70b-versatile`. **Rejected** — it emits its native `<function=...>` text format instead
of JSON tool calls, which Pydantic AI can't parse.

## SearchApi.io over Amadeus/Duffel/Skyscanner/raw scraping, for flights
`flights_searchapi.py` calls SearchApi.io's Google Flights engine. **Alternative 1:** Amadeus or
Duffel — enterprise flight APIs with real booking capability. **Rejected** — both gate access
behind a business/partner approval process, which doesn't fit a take-home's signup-and-go
timeline. **Alternative 2:** scrape Google Flights directly. **Rejected** — no stable schema, no
free-tier guarantee, and fragile to markup changes. **Chosen because:** free tier at signup,
Google Flights data already normalized into structured JSON, and the response's `best_flights`
array is pre-ranked cheapest-first by SearchApi.io itself — the app's own `cheapest_first`
guarantee (see "Cheapest flights" in [ARCHITECTURE.md](ARCHITECTURE.md)) reinforces this rather
than building price-sort/filter logic from scratch against raw, unranked results.

## Tavily over Serper/Bing/SerpAPI/Google Custom Search, for activity research
`activities_tavily.py` calls Tavily for the itinerary's activity research. **Alternative:**
general-purpose search APIs (Serper, Bing Search API, SerpAPI, Google Custom Search) — cheaper or
more familiar, but return raw SERPs (titles/snippets/links) that need extra parsing before an LLM
can use them reliably. **Rejected** — that parsing step is exactly the kind of brittle scraping
this project's "real data only, honest degradation" principle (below) tries to avoid. **Chosen
because:** Tavily is purpose-built for LLM agents — free tier at signup, and results come back
already shaped for grounding a model's output (clean content + source URL per result), which is
what `web_search`'s citation requirement (every activity cites a real URL) needs directly.

## Real data only, honest degradation
Adapters never fabricate. On quota/rate-limit/empty they return cached real data if present, or an
honest `unavailable_reason` — never an invented offer or activity. Booking-options fetches
(`departure_id`/`arrival_id`/`outbound_date` forwarded alongside `booking_token`, all derived from
the flight's stored `raw_offer`) work end-to-end for one-way and round-trip alike. Round-trip
offers store a `departure_token`, not a real `booking_token` (see `_parse_offers`); resolving it
costs one extra SearchApi call (`_resolve_return_booking_token`) that fetches the return-leg
options and picks the cheapest — the current UI has no separate return-flight-selection step, so
this is the same cheapest tie-break the rest of the app already uses. Any failure in that
resolution degrades honestly to no booking links, same as the rest of the booking-options path.

## Custom Slack HITL adapter over chat-sdk-python
`app/adapters/slack_hitl.py` hand-rolls signature verification (stdlib `hmac`/`hashlib`) and Block
Kit message building for one outbound POST and one signed callback. **Alternative:**
[`chat-sdk-python`](https://github.com/Chinchill-AI/chat-sdk-python), a multi-platform (Slack,
Discord, Teams, Telegram, WhatsApp, and more) async chat SDK — trustworthy prior art, built by a
former colleague (30+ years as a SWE, enterprise background) from a previous role, with its own
tested Slack webhook verifier and cross-platform `Card`/`Button` model already covering this exact
surface. **Rejected for this
deliverable** — pulling in a 9-platform, alpha-status SDK for a single Slack button is more
integration risk than the feature warrants, and hand-rolling the ~30-line HMAC check against
Slack's own documented example is a clearer demonstration of understanding the protocol than
depending on an abstraction over it. **Kept as the deliberate extension point:** `notify_pending_approval`/`resolve_approve`/`resolve_reject` are isolated behind `slack_hitl.py`'s
narrow interface specifically so that a real multi-connector future (Discord, Teams, ...) is a
module swap to `chat-sdk-python`, not a rewrite — see `docs/SLACK_SETUP.md`.

**Local dev needs a public callback URL.** Slack's Interactivity config can't POST to
`localhost`, so `POST /api/slack/interactions` has to be reachable from the internet even during
local development. `ngrok http 8000` is the tunnel used for this (see `docs/SLACK_SETUP.md`) —
no code depends on ngrok specifically, any HTTPS tunnel pointed at port 8000 works the same way.

## Slack approve only confirms; execute stays in the frontend
The Slack callback (`routes/slack.py`) calls only `confirm_booking`/`reject_booking`, never
`execute_booking`. **Alternative:** let Approve in Slack also execute the booking.
**Rejected** — Slack requires an ack within 3 seconds, and execute calls SearchApi and can take up
to `SEARCHAPI_TIMEOUT_SECONDS`; execute stays behind the frontend's existing Execute action, where
the UI already discloses ("your flight hasn't been purchased") what execute does and doesn't do.

## Connector enablement is a DB-backed toggle, not just an env var
`connector_setting.slack_enabled` is a single-row table flipped via `/api/connectors`, separate
from whether Slack credentials exist in settings. **Alternative:** treat "credentials present" as
"enabled." **Rejected** — that collapses configuration and intent into one flag, so any deployment
with the env vars set would silently start posting to Slack; the toggle lets an operator configure
Slack once and still flip it off at runtime without a restart or an env change.

## Trip history is a list endpoint plus a global execution feed, not per-trip-only
`GET /api/trips` lists every trip the (single demo) user owns, newest first; `GET /api/execution`
mirrors that scoping across every `AgentRun` those trips own, also newest first — both live in
`trips_repository.py` next to the existing per-trip `get_trip`/`get_execution_panel`, sharing the
same user-scoping query rather than introducing a second concept. **Why global execution, not just
per-trip:** the frontend used to require a trip to be "active" (in `localStorage`) before its
execution history was visible at all — switching trips, or never having picked one, hid the tab
entirely. A tab that only ever shows one trip's history can't answer "what has this agent done,
period," which is the actual job of an execution/observability view. **Alternative considered:**
keep it per-trip and add a trip switcher. **Rejected** — that still frames execution history as a
property of one trip instead of an audit trail, and duplicates the trip-switching UI the new
"Your trips" tab already provides. **Filtering is client-side, not query params:** both the route
search and status filter on the execution tab, and the date-range filter on "Your trips," filter
the already-fetched list in the browser rather than adding query parameters to either GET. At this
scale (one user, dozens of trips) a server-side filtered query is speculative infrastructure;
revisit if the trip count grows enough that shipping every trip/run to the client stops being
cheap. The one thing kept server-side either way: user-scoping, since that's a security boundary,
not a display preference.

## Rate limiting protects scarce third-party quota
`enforce_request_rate_limit` (`app/rate_limit.py`) applies a per-IP request cap plus a global
concurrency cap on real LLM calls, gating `/plan` and `/flights/search`. **Alternative:** no
limiting, rely on each provider's own rate-limit response. **Rejected** — SearchApi's free tier
is a one-time search allotment, not a renewing rate limit, so a burst of retries (accidental
double-clicks, a buggy client) would permanently burn quota rather than just wait out a window;
limiting at the app boundary protects that budget before a request ever reaches SearchApi.

## Added vs. deferred, to stay in scope
Two things were added **beyond** the take-home's minimum ask, deliberately, as scoped "strong
plus" bonuses: HITL booking (see "HITL booking is a REST state machine" above) and the optional
Slack connector (see "Custom Slack HITL adapter" below) — both explicitly named in the take-home
brief as differentiators, not required. Both were kept narrow on purpose (booking is a *handoff*,
not a real purchase; Slack is one workspace, one button, hand-rolled instead of a multi-platform
SDK) so the bonus demonstrates the pattern without ballooning into a second project.

Everything below was considered and **deferred**, not attempted-and-abandoned — each pays off
across many sessions or needs infrastructure a take-home doesn't have, and building it now would
be scope creep against the actual ask: episodic/semantic/procedural agent memory, full auth (only
`get_current_user` changes to support it later), payment processing, and Saga-style compensation
for a multi-step real airline booking (see "Durable execution, not Saga" in `ARCHITECTURE.md`).
Trap-doors are left where they'd slot in, so "deferred" means a known extension point, not a gap
nobody thought about.
