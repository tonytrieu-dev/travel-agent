# Evals

`backend/evals/` (`dataset.py`, `evaluators.py`, `run.py`) tests the planner agent's behavior, not
just its code. The default suite contains deterministic evaluators only. Run it like a test suite
(`uv run python -m evals.run --repeat 3`, alongside `pytest`) before considering a change to the
agent, prompt, or tools done.

`FitnessAppropriateness` is optional because it uses an LLM judge. Include it explicitly with
`uv run python -m evals.run --with-judge` (and add `--repeat 3` when repeat runs are wanted).

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
| **This project's evaluators** | `OutputTypeMatches`, `CitationGrounding`, `NoFlightActivities`, `FlightSearchTrajectory`, `WebSearchTrajectory`, `LowFitnessSafety`, `PhysicalLoad`/`PhysicalLoadComparisons` | `FitnessAppropriateness` |

**Every safety-relevant property is exact, not judged.** `LowFitnessSafety` — "never hand a
low-fitness traveler a strenuous activity" — is an equality check against
`UNSAFE_INTENSITY_FOR_LOW_FITNESS` in `app/agent/planner.py`, not an LLM judge call. An LLM judge is a
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
here: a human doesn't scale to being a *repeatable regression check*. When enabled, the judge runs
against 4 cases × 3 repeats = 12 samples per invocation, and is meant to be re-run after every
prompt/model/tool change — a human re-grading every run isn't a realistic substitute for that,
it's a one-time calibration step. The LLM judge is the cheap, repeatable proxy; a human spot-check against the
judge's rubric (`build_fitness_appropriateness_judge` in `evaluators.py`) is how you confirm the
proxy is actually measuring what a person would flag, before trusting it to run unattended.

## Evaluation pipeline

| Stage | This project |
|---|---|
| **1. Test set** | `dataset.py` — 4 cases crossing traveler age (24, 78) × fitness level (low, high), same JFK→SAN route/dates |
| **2. System version** | `gpt-oss-120b` on Cerebras, current prompt (`app/agent/prompts.py`), `search_flights`/`web_search` tools |
| **3. Evaluator** | 7 exact evaluators by default, plus the `PhysicalLoadComparisons` report evaluator; optional `LLMJudge` with `--with-judge` |
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

The recorded suite expects exactly one successful `search_flights` call with the case's route and
dates, plus exactly one successful broad, non-flight `web_search` call for destination activities.

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

## Output reliability fix

Four reliability issues surfaced by running the live eval repeatedly, not by unit tests:

1. **Duplicate tool calls.** The model sometimes called `search_flights` twice per trip (searching
   the return leg separately, even though the first response already covers both legs) and
   `web_search` more than once, burning tool-call and token budget for no new information.
   `PlannerDeps` now tracks a per-run `_search_flights_called` / `_web_search_called` flag; a
   second call within the same run raises `ModelRetry` instead of re-hitting the provider.
2. **Free-text intensity.** `ActivityOut.intensity` was `str`, and the model wrote descriptive
   phrases ("low to moderate (tram seated)") that no downstream safety check could match against a
   fixed term list. It's now `Literal["low", "moderate", "high"]`, so pydantic rejects an
   out-of-vocabulary value at parse time — and the system prompt now states the closed vocabulary
   directly, so the model gets it right on the first attempt instead of relying on a `ModelRetry`
   correction loop (each retry resends the full conversation, which is what was driving both
   `UsageLimitExceeded` and `Exceeded maximum output retries` failures).
3. **Output envelope mismatch.** Left on `auto`, pydantic-ai picks native structured output for
   `gpt-oss-120b` and wraps the `ItineraryOut | ClarificationOut` union in `{"result": {"kind":
   ...}}`. A live run showed all three output retries were the model fumbling that envelope
   (missing `"result"`, missing `"result.kind"`, then a bad `"kind"` literal), never a real
   validator rejection — and each retry resends the full itinerary, so one run died at 32,882
   tokens against Cerebras's 30,000/minute limit. Fixed by pinning `output_type` to
   `[ToolOutput(ItineraryOut), ToolOutput(ClarificationOut)]` in `app/agent/planner.py`, removing
   the envelope ambiguity entirely.
4. **Prose instead of a tool call.** Pinning tool output above closed the envelope retries but
   surfaced a new one: the model sometimes replied with the itinerary as plain text instead of
   calling the result tool. Fixed by naming the delivery contract directly in the system prompt
   (`AGENTS.md`): "Deliver your final answer by calling the result tool for it... Never write the
   itinerary or the question as plain text in a reply."

**Live-verified result, all four fixes in place** (`--repeat 3 --with-judge`, 12 case-runs,
`gpt-oss-120b` via Cerebras, recorded flight/activity fixtures + live LLM calls): all 12 case-runs
completed cleanly — no `UsageLimitExceeded`, no exhausted output retries. 84/84 deterministic
assertions passed (100%), and all four `PhysicalLoadComparisons` rows passed with real samples on
both sides of every comparison (previously blocked by the retry exhaustion above starving 3 of the
4 age/fitness buckets of any completed sample). The `--with-judge` evaluator itself,
`FitnessAppropriateness`, passed 11/12: `age_78_high_fitness [3/3]` failed with the same
`physical_load` score as a *passing* run for the same case, because the itinerary leaned on
shuttles/rest stops/minimized walking despite the traveler's stated high fitness level — a genuine
tone/pacing miss the numeric score can't see, which is exactly the class of failure
`FitnessAppropriateness` exists to catch (see "Why an LLM judge instead of a human judge" above).
Treat the token-budget/retry-exhaustion failure mode as closed; treat the one judge failure as a
real, open model-behavior finding, not a harness bug.

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
