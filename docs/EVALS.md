# Evals

`backend/evals/` (`dataset.py`, `evaluators.py`, `run.py`) tests the planner agent's behavior, not
just its code. Run like a test suite (`uv run python -m evals.run --repeat 3`, alongside `pytest`)
before considering a change to the agent, prompt, or tools done — there's no CI pipeline for this
take-home, so that discipline is the substitute.

## Why evals, not just unit tests

Unit tests check that functions return what they're supposed to given fixed input. The planner
agent doesn't have that property: the same prompt can legitimately produce different tool-call
sequences and itinerary wording across runs, because an LLM sits in the loop. A unit test that
asserts on exact output would be flaky by construction. Evals instead assert on *properties* the
output must have regardless of exact wording — grounded in a real search result, safe for the
traveler's fitness level, within a tool-call budget — and run each case multiple times
(`--repeat 3`) so a pass isn't one lucky sample.

## Exact evaluation vs. subjective evaluation

Every evaluator in `evaluators.py` falls into one of two families, and the split is deliberate:

| | Exact (objective) | Subjective |
|---|---|---|
| **What it checks** | Deterministic, verifiable outcomes | Quality that doesn't reduce to a rule |
| **How** | Plain code against the recorded tool-call trace / output shape | An `LLMJudge` (Gemini) scoring against a written rubric |
| **This project's evaluators** | `OutputTypeMatches`, `CitationGrounding`, `NoFlightActivities`, `FlightSearchTrajectory`, `WebSearchTrajectory`, `ToolCallBudget`, `LowFitnessSafety`, `PhysicalLoad`/`PhysicalLoadComparisons` | `FitnessAppropriateness` |

**Every safety-relevant property is exact, not judged.** `LowFitnessSafety` — "never hand a
low-fitness traveler a strenuous activity" — is a regex over intensity strings
(`_is_unsafe_intensity` in `app/agent/planner.py`), not an LLM judge call. An LLM judge is a
probabilistic scorer: it can be right on average and still miss an individual unsafe case, which
is an acceptable property for a *quality* signal and not an acceptable one for a *safety*
guardrail. The rule mirrors the project's broader stance on using the model only for genuine
judgment calls (see the agent's own `output_validator`s, which enforce the same properties
structurally at generation time — the eval and the guardrail check the same thing twice,
independently).

`FitnessAppropriateness` is the one place quality genuinely doesn't reduce to a rule — "is this
itinerary's intensity and pacing actually well-suited to a 24-year-old vs. a 78-year-old" is a
judgment call, which is exactly the category `LLMJudge` is for.

## Why an LLM judge instead of a human judge

A human is more accurate per sample than an LLM judge. That's not actually the deciding factor
here: a human doesn't scale to being a *repeatable regression check*. This suite runs 4 cases ×
3 repeats = 12 samples per invocation, and is meant to be re-run after every prompt/model/tool
change — a human re-grading every run isn't a realistic substitute for that, it's a one-time
calibration step. The LLM judge is the cheap, repeatable proxy; a human spot-check against the
judge's rubric (`build_fitness_appropriateness_judge` in `evaluators.py`) is how you confirm the
proxy is actually measuring what a person would flag, before trusting it to run unattended.

## Evaluation pipeline

| Stage | This project |
|---|---|
| **1. Test set** | `dataset.py` — 4 cases crossing traveler age (24, 78) × fitness level (low, high), same JFK→SAN route/dates |
| **2. System version** | `gpt-oss-120b` on Cerebras, current prompt (`app/agent/prompts.py`), `search_flights`/`web_search` tools |
| **3. Evaluator** | 8 exact evaluators + 1 `LLMJudge` (see split above) |
| **4. Scores** | Pass/fail per assertion, plus a `physical_load` metric (sum of activity intensities) |
| **5. Decision** | Manual: a case failure or judge failure means don't ship that change until understood |
| **6. Monitoring** | Not built — deferred; the take-home's Agent Execution Panel (`docs/ARCHITECTURE.md`) is the closest analog, but it observes production runs, not eval regressions |
| **Learn → update evals** | See the recorded-fixture bug below — a real example of this loop closing |

## Provider modes

`run.py` supports two modes, chosen for different reasons:

- **`recorded` (default)** — real *captured* SearchApi/Tavily payloads replayed from
  `tests/fixtures/recorded/`, but the LLM call itself is always live (Cerebras has no cassette
  equivalent — the model's tool-selection and generation *is* what's under test). This is what
  `--repeat 3` runs against: deterministic third-party data isolates evaluator failures to the
  model's behavior, not provider flakiness.
- **`--live-smoke`** — real APIs end-to-end, one case, no repeats. Spends real quota
  (SearchApi's search allotment is a one-time 100-search budget, not a renewing rate limit — see
  `docs/DECISIONS.md`), so it's a manual, occasional check that recorded fixtures still match
  reality, not something run routinely.

## A fixture bug the evals caught, in themselves

The recorded cassette `tests/fixtures/recorded/flights/JFK_SAN_2026-09-01_2026-09-08.json`
originally held only the *first* step of SearchApi's round-trip flow (an outbound-only response
with a `departure_token`, no `return_flights`). `RecordedProvider` correctly refuses to fabricate
a return-leg pairing it was never given (see `test_recorded_provider_does_not_expose_unpaired_round_trip_offers`
in `tests/test_flight_provider_strategy.py`), so every recorded round-trip search for that route
returned zero offers. The agent, seeing no offers, retried `search_flights` with varied arguments
until it burned through the tool-call budget and the token budget, then failed to produce a
grounded itinerary — cascading into most of a `--repeat 3` run failing with
`UnexpectedModelBehavior`/`UsageLimitExceeded`, not a clean evaluator failure.

The fix: capture the real second step (SearchApi's `departure_token` → booking-options resolution,
the same pairing `LiveSearchApiProvider._pair_round_trip_offer` does live) and store the *paired*
result as the cassette, matching the shape `RecordedProvider` expects. This is the "learn and
update evals" loop in practice — the eval didn't just fail the agent, it surfaced a gap in the
eval's own recorded data, which running the suite end-to-end (rather than trusting individual unit
tests in isolation) is what exposed.

## A confirmed reliability gap: output-schema envelope retries

`--repeat 3` on the fixed dataset still fails 9 of 12 case-runs (75%) — but no longer from the
flight-search loop above; `FlightSearchTrajectory` now passes 100% of completed runs. The new
failures are `UnexpectedModelBehavior: Exceeded maximum output retries (3)` and
`UsageLimitExceeded`. Root-caused by hooking `agent.iter()` directly on a single failing case
(`age_78_low_fitness`) and logging every node, tool call, and `RetryPromptPart` — not guessed at:

1. **Attempt 1** (plain text, not a tool call): the model writes `{"days": [...]}` — missing the
   `result` envelope pydantic-ai's union output type (`ItineraryOut | ClarificationOut`) requires.
   Retry: *"Field required at ('result',)"*.
2. **Attempt 2**: `{"result": {"days": [...]}}` — still missing the discriminator fields. Retry:
   *"Field required at ('result','kind')/('result','data')"*.
3. **Attempt 3**: the envelope is finally correct — `{"result": {"kind": "ItineraryOut", "data":
   {...}}}` — but Day 1/Day 7 list "Arrive at San Diego International Airport" and "Check-out and
   transfer to SAN airport" as itinerary activities. `reject_flight_activities` correctly fires:
   *"Flights are not itinerary activities... remove them."*
4. Before a 4th attempt completes, the run dies with `UsageLimitExceeded` (33,132 > 30,000
   tokens) — each retry resends the entire prior (large) itinerary back into context, so 2 retries
   spent purely on envelope-shape guessing compound token usage fast, leaving no budget for the
   3rd retry (the real content fix) to land.

Two things are true at once, and worth separating: **`reject_flight_activities` is not the bug —
it's the guardrail working exactly as designed.** The system prompt already says not to list
flights as activities; the model did it anyway; the guardrail caught it, the same "the prompt
alone doesn't hold, enforce it structurally" reasoning behind every other `output_validator` in
`planner.py`. The actual gap is upstream: `gpt-oss-120b` sometimes emits the final structured
output as free text instead of a clean tool call, and burns 2 of its 3 output retries just
guessing pydantic-ai's envelope shape before it can spend a retry on real content. This is a
specific, evidenced free-tier-model reliability limitation, not a design flaw in the eval, the
guardrails, or the fixture — and it trades off directly against an existing documented decision
(`config.py`'s comment: don't raise `MAX_CONTEXT_TOKENS` past Cerebras's real 30K/min limit, that
just swaps a clean `UsageLimitExceeded` for a raw mid-run 429). A targeted fix — forcing
`NativeOutput`/`ToolOutput` mode explicitly on the agent instead of relying on pydantic-ai's
default text-fallback for this model — is a real, untested code change, deliberately not made
under take-home time pressure without a live-eval run to confirm it actually helps rather than
surfacing a different Cerebras/gpt-oss quirk. Left here as a known, well-understood gap rather than
a silently-passing green suite.

## Enterprise scalability, security, and integration

This take-home is built with enterprise use cases in mind, not just a working demo — the choices
below hold up against the kind of scrutiny a real organization would apply before adopting an
agentic system: can it scale, is it secure, and does it integrate cleanly with the platforms and
governance an enterprise already runs. Below is an honest mapping of what this project actually
demonstrates on each axis, pointing at `ARCHITECTURE.md`/`DECISIONS.md` rather than repeating them.

**Scalability.** `MAX_CONCURRENT_AGENT_RUNS` caps concurrent real LLM calls; per-IP rate limiting
(`RATE_LIMIT_MAX_REQUESTS`/`RATE_LIMIT_WINDOW_SECONDS`) protects the scarce SearchApi one-time
quota (see "Rate limiting protects scarce third-party quota" in `DECISIONS.md`); `MAX_TOOL_STEPS`/
`MAX_CONTEXT_TOKENS` bound the agent loop to the model provider's real rate limit instead of
degrading into a 429 mid-run; DBOS checkpoints the planner run and booking execute so a crash
resumes instead of losing state. The evals themselves scale independently of production traffic —
`recorded` mode replays captured third-party payloads so `--repeat 3` costs zero external quota,
which is what makes running the suite before every change *practical* rather than something that
competes with production's own scarce API budget.

**Security.** Secrets are `SecretStr`-wrapped (never logged/repr'd); the HITL booking gate is
structural, not a prompt the model could be talked out of — the agent has no tool that can move
booking state (see "The agent has only two read-only tools" in `DECISIONS.md`); audit tables
(`booking_transition`, `execution_event`) are append-only at the Postgres trigger level, not just
by application convention; the Slack callback verifies its signature with constant-time `hmac`.
The one place untrusted external content enters the model's context is `web_search` results (a
third-party API response, not user chat — there's no direct chat surface to this agent), and
`sanitize_web_content` wraps that content in an explicit untrusted-data delimiter before it reaches
the prompt so an embedded instruction reads as quoted data, not a directive (unit-tested in
`tests/test_prompt_injection_sanitizer.py`). **Honest gap:** that guardrail is unit-tested at the
function level, not yet exercised through an eval case that feeds the agent adversarial web content
end-to-end — a natural next addition if this surface becomes higher-stakes than a take-home.

**Integration with a real enterprise's platform.** Enterprises adopting AI generally converge on
the same concerns regardless of industry: platform governance, access management, observability,
compliance, and cost tracking (FinOps) across a standardized internal stack. This project can't
assume any specific enterprise's internal stack, but it demonstrates the same *principles* on its
own concrete stack: the Agent Execution Panel (`ARCHITECTURE.md`) is the observability layer —
real persisted `agent_run`/`agent_run_step`/`execution_event` rows, not live in-memory state; the
DB-backed connector toggle (`connector_setting`) is a governance pattern (credentials present ≠
enabled, runtime-flippable without a redeploy — the same shape as an enterprise feature-flag/
approval gate); rate limiting is the FinOps-adjacent piece (usage capped at the app boundary
before it burns provider quota). The honest framing: dropped into an organization with its own
approved AI platform, the Cerebras/SearchApi/Tavily-specific adapters would swap out, but the
DI/Strategy/Repository/FSM patterns underneath them (`ARCHITECTURE.md`'s "Architectural patterns"
section) are what would carry over, since they're what make a provider swap a module change
instead of a rewrite.

**RAG / vector databases — not used here, and why that's the right call, not a gap.** This project
doesn't use a vector database. Activity data comes from live `web_search` (Tavily), not a static
indexed corpus — there's nothing to embed and search offline, and grounding every activity in a
real-time source URL (`CitationGrounding` in `evaluators.py`) is the correctness property that
actually matters here, not retrieval latency. The honest place vector search *would* fit in an
enterprise setting: a static, high-volume internal corpus — internal documentation, policy text,
prior tickets — is exactly the shape RAG is built for, unlike this project's live travel data.
