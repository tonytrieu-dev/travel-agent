# Decisions

Load-bearing choices, each with the alternative and why it was rejected.

## HITL booking is a REST state machine, not an agent tool
The booking write moves through `PENDING → CONFIRMED → EXECUTED` (or `CANCELLED`/`EXPIRED`) via
explicit `/bookings/*` calls driven by human clicks. **Alternative:** expose booking as an agent
tool gated by an approval prompt. **Rejected** because a prompt-gated tool makes "a human
confirmed first" a prompt-dependent hope; a state machine outside the agent makes it structural —
the agent has no tool that can move booking state.

## Agent output is a union: `Itinerary | ClarificationOut`
Missing age/fitness produces real clarifying questions, not a guessed itinerary. **Alternative:**
always return an itinerary and let the prompt beg the model to ask. **Rejected** — "ask, don't
assume" as a type is enforced by validation; as prose it's optional.

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
`llama-3.3-70b-versatile`: 1,000 requests/day vs Gemini free tier's 20, with strong tool-calling
and the highest per-minute token headroom of Groq's capable models. Swap the model in
`config.py::GROQ_MODEL`.

## Real data only, honest degradation
Adapters never fabricate. On quota/rate-limit/empty they return cached real data if present, or an
honest `unavailable_reason` — never an invented offer or activity. Booking-options for round-trip
fares (which need a second SearchApi call to resolve a `departure_token`) currently fail honestly
rather than spend the one-time search quota on a feature beyond the assignment's "strong plus."

## Deferred by design
Episodic/semantic/procedural agent memory, full auth (only `get_current_user` changes), and
payment processing — each pays off across many sessions or needs infrastructure the take-home
doesn't. Trap-doors are left where they'd slot in.
