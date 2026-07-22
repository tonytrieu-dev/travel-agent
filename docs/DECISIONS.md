# Decisions

Load-bearing choices, each with the alternative and why it was rejected.

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
entirely instead of just making it reliable. Scoped to the API boundary only: `TripRequest.age`/
`.fitness_level` stay nullable in the DB so existing incomplete rows keep reading fine.

## Two read-only tools + a fail-closed tool gate
Only `search_flights` and `web_search`, both `READ_ONLY`. Registration requires a classification;
a `BOUNDARY_CROSSING` tool with no approver channel is denied, never executed. **Alternative:**
register tools directly on the agent. **Rejected** — the gate makes wiring a write tool fail
closed and keeps a regression test (`test_tool_gate.py`) honest.

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

## Groq over Gemini
Groq's free tier: 1,000 requests/day vs Gemini's 20. Model within Groq: `openai/gpt-oss-120b`, not
the initially-chosen `llama-3.3-70b-versatile`. **Rejected llama-3.3-70b** — observed live, it
intermittently emitted its native `<function=...>` text format instead of a JSON tool call, which
pydantic-ai can't parse and crashes with "Exceeded maximum output retries." `gpt-oss-120b` parses
cleanly. Swap the model in `config.py::GROQ_MODEL`.

## Real data only, honest degradation
Adapters never fabricate. On quota/rate-limit/empty they return cached real data if present, or an
honest `unavailable_reason` — never an invented offer or activity. One-way booking-options fetches
(`departure_id`/`arrival_id`/`outbound_date` forwarded alongside `booking_token`, all derived from
the flight's stored `raw_offer`) work end-to-end. Round-trip fares still fail honestly — resolving
their `departure_token` into a real `booking_token` needs a second SearchApi call, which stays
deferred rather than spend the one-time search quota on a feature beyond the assignment's "strong
plus."

## Deferred by design
Episodic/semantic/procedural agent memory, full auth (only `get_current_user` changes), and
payment processing — each pays off across many sessions or needs infrastructure the take-home
doesn't. Trap-doors are left where they'd slot in.
