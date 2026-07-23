# Evaluation Improvement Plan for the Travel Agent Take-Home

## Purpose

This document turns the lessons from the supplied **Agent Evals Master Class** slides into an
implementation plan for this repository. It is intended as a handoff to Claude Code.

The goal is not to make the eval suite large. The goal is to make a small suite answer the
questions a reviewer will actually care about:

1. Does the bot satisfy every take-home requirement?
2. Did it reach the answer through the correct tools and safety gates?
3. Is it reliable across repeated runs?
4. When it fails, can the report show exactly where and why?
5. Can two runs be compared without hidden changes in prompts, fixtures, providers, or code?

## Executive assessment

The current eval suite has a good foundation, but it is too narrow to demonstrate the take-home.
It contains only two cases on one route and scores only:

- output union type;
- exact citation URL membership in `web_search` results; and
- fitness appropriateness through an LLM judge.

That proves a slice of itinerary quality. It does **not** currently prove the most literal flight
requirement, correct tool trajectory, reliability, cost, failure recovery, clarification behavior,
booking safety, or UI quality.

The most important product/eval finding is this:

> The current backend and frontend preserve provider order for flight offers. They do not sort by
> `price_usd`, mark a cheapest offer, or test that the cheapest offers are surfaced first.

The take-home explicitly says **“Find the cheapest flights.”** This should be treated as a P0
compliance gap. The new system acceptance test should fail before the product change is made.

## What the current implementation already does well

Preserve these strengths:

- `CitationGrounding` reads real tool-return history rather than trusting the final answer.
- The planner structurally rejects activity URLs that were not returned by `web_search`.
- The CLI already supports repeated runs with `--repeat`.
- The runner fingerprints the model, prompt, dataset, and Git commit.
- The agent has strict structured output and read-only tool registration.
- SearchApi and Tavily degrade honestly instead of fabricating data.
- HITL booking is enforced outside the model through a state machine and already has strong BDD
  coverage for confirm-before-execute, expiry, upstream failure, and double execution.
- Agent/tool execution is persisted, so richer trajectory evaluators can be added without a new
  observability architecture.

## Lessons applied from the slides

The supplied slides recommend the following evaluation shape, which fits this project well:

- Evaluate **one real domain job**, not a generic chatbot.
- Treat the whole configured agent as the thing under test: prompt, model, tools, policies, and
  harness.
- Give one answer multiple verdicts: **outcome, trajectory, cost, reliability, safety, and
  experience**.
- Prefer deterministic code graders wherever code can decide; use a judge only for subjective
  properties.
- Compute expected answers from the same deterministic fixture/query layer used by the tools;
  do not type fragile gold answers by hand.
- Repeat the same items and report both “worked at least once” and “worked every time.”
- Separate confidently wrong answers from honest abstentions/unavailable results.
- Grade long chains by checkpoints so a partial failure remains diagnosable.
- Inject controlled faults and score recovery.
- Fingerprint every comparable run and persist results over time.
- Use a small set of sharp items on every meaningful change rather than a huge suite rarely run.

## Do not put every requirement into `pydantic-evals`

Use three layers. This keeps each assertion at the cheapest, most deterministic layer that can
answer it.

| Layer | What it proves | Tooling |
|---|---|---|
| Agent eval | Itinerary outcome, tool trajectory, pacing, grounding, clarification, recovery, usage | `backend/evals/` with `pydantic-evals` |
| System acceptance | Cheapest-flight ordering, provider arguments, caching, API contracts, HITL state transitions | Gherkin + pytest |
| UI/reviewer evidence | Cheapest badge/order, visible confirmation gate, accessibility, responsive and pleasant presentation, API/protocol explanation | Frontend tests where practical plus a short manual review checklist |

Booking is intentionally not an agent tool. Do not weaken that architecture merely to place
booking inside an agent eval. Grade the planner as an agent and grade booking as a deterministic
stateful workflow.

## Current gaps, prioritized

### P0: directly affects take-home compliance

#### 1. Cheapest flights are neither enforced nor evaluated

Evidence:

- `backend/app/repositories/trips_repository.py::search_flights` persists offers in upstream order.
- `frontend/src/components/FlightSearch.tsx` filters offers but does not sort them.
- `backend/evals/` has no flight price/order evaluator.
- The current eval dates (`2026-09-01` to `2026-09-08`) do not match the only recorded flight
  cassette (`2026-08-15` to `2026-08-22`), so a recorded-mode eval cannot exercise real offers.

Required test-first change:

1. Add a Gherkin scenario to `backend/features/trip_planning.feature` stating that unordered
   provider results are returned in ascending `price_usd` order.
2. Add a red step/test using at least three offers in deliberately non-price order.
3. Implement deterministic ascending ordering in the repository response and cache reads.
4. Add a frontend assertion or component test that the first visible offer is cheapest and is
   visibly labeled `Cheapest`.
5. If the UI derives the badge from the first result, keep ordering a backend invariant so all
   clients receive the same guarantee.

Do not add an `is_cheapest` API field unless needed. If one is added, follow repo SDD order:
`backend/specs/openapi.yaml` first, then Gherkin, then red/green implementation.

Acceptance criteria:

- For prices `[812, 499, 640]`, the API returns `[499, 640, 812]`.
- Cached and live paths use the same order.
- The UI labels the `$499` offer as cheapest.
- Filtering by stops preserves ascending price order within the filtered set.

#### 2. The eval dataset is too small and confounds age with fitness

The two current cases compare a 24-year-old/high-fitness traveler with a 78-year-old/low-fitness
traveler. If their itineraries differ, the evaluator cannot tell whether age, fitness, or both
caused the difference.

Replace this with a small factorial/metamorphic group on the same route, destination, dates, and
activity evidence:

- age 24, fitness low;
- age 24, fitness high;
- age 78, fitness low; and
- age 78, fitness high.

Add pairwise evaluators:

- changing only fitness from high to low must not increase total itinerary intensity;
- changing only age from 24 to 78 should not increase walking/physical load without an explicit
  accessibility justification;
- low fitness must contain no high/strenuous activity;
- high fitness may receive active options but must not be forced into unsafe intensity merely to
  create contrast.

This tests whether both inputs actually influence the plan instead of merely checking two
stereotyped endpoints.

#### 3. The suite grades final output but barely grades trajectory

Add deterministic trace evaluators for every normal itinerary case:

- `search_flights` called exactly once, or a documented cached result was reused;
- `search_flights` received the exact origin airport, destination airport, departure date, and
  return date from the case;
- `web_search` called between one and three times;
- `web_search` queries are destination/activity research, not flight prices, times, or schedules;
- no flight is added as an itinerary activity;
- all activities are grounded in results from the same run;
- total tool calls remain within the configured limit;
- the agent does not ask for optional preferences when required trip inputs are complete.

The runner currently exports only web-search URLs into eval attributes. Replace that narrow
attribute with a structured, JSON-serializable trace summary containing:

```text
tool_calls: ordered list of tool name, arguments, result status, and compact result metadata
web_search_results: URL, title, and sanitized content by call
flight_offers: normalized offers returned by search_flights
usage: input tokens, output tokens, requests, and tool calls
duration_ms: whole run and tool durations when available
retry_count: model/output/tool retries when observable
```

Keep the raw model message history out of stable reports if it may contain large third-party
content. Store compact evidence sufficient to explain each score.

#### 4. Core evals depend on live Tavily behavior

The baseline should be repeatable and cheap. Introduce two modes:

- **Recorded deterministic baseline**: recorded real SearchApi and Tavily responses; safe for
  repeated local/CI runs and capable of computing gold facts from fixtures.
- **Live smoke eval**: a very small, explicitly opt-in run against free APIs to detect provider
  schema/integration drift.

Do not fabricate fixtures. Capture real provider responses, sanitize secrets, version them, and
hash them into the run fingerprint.

Align case dates with recorded flight fixtures or capture a new matching cassette. A baseline
case whose flight tool always reports “no recorded cassette” cannot prove flight search quality.

### P1: makes results trustworthy and diagnosable

#### 5. URL membership is necessary but not sufficient grounding

`CitationGrounding` passes whenever an activity uses any URL returned by `web_search`, even if the
page is unrelated to the named activity. Preserve this deterministic membership check, then add
an `ActivitySupportedBySource` evaluator that receives the exact title/content associated with
each URL and checks whether the named activity is actually supported.

Use code first where possible:

- source URL appeared in the run;
- URL is non-empty and HTTP(S);
- a flight result URL is never used as an activity citation;
- every activity has exactly one traceable source result.

Use a calibrated judge only for semantic support between activity name/description and the
source title/content. The judge must not use general world knowledge to rescue an unsupported
citation.

#### 6. No reliability aggregation is reported

Keep `--repeat 3`, but aggregate repeats by item and metric. Report:

- `pass@k`: at least one of the `k` attempts passed;
- `pass^k`: all `k` attempts passed;
- flakiness gap: `pass@k - pass^k`;
- mean score where a metric is continuous;
- confidently wrong count;
- honest unavailable/abstention count; and
- run-error count.

The take-home baseline should use `k=3` for the sharp core set. A single lucky pass is not enough.

#### 7. The same model grades its own output without a recorded calibration result

`build_fitness_appropriateness_judge()` uses the same Cerebras model as the planner. This may be
acceptable under free-tier constraints, but the report must not treat it as trusted by default.

Preferred order:

1. Move all objective checks to deterministic evaluators.
2. Make the remaining judge rubric criterion-specific, with explicit pass/fail/unknown guidance.
3. Hand-label 20–30 representative itinerary outputs, including difficult and borderline cases.
4. Measure per-criterion agreement, false positives, and false negatives.
5. Record the calibration dataset hash and agreement result.
6. Prefer a different judge model/provider if a free option is available; otherwise disclose that
   the judge shares the agent model and retain the calibration gate.

Do not use one broad judge prompt for fitness, clarity, grounding, and safety. Smaller graders
produce more useful failures.

#### 8. Fingerprints miss important sources of drift

The current fingerprint is useful but incomplete. `git_sha` does not describe uncommitted changes,
and provider/fixture/config drift can change results without changing the dataset text.

Add:

- `git_dirty` and, ideally, a hash of the working-tree diff for relevant files;
- judge model and judge rubric hash;
- `uv.lock` hash;
- recorded provider fixture hashes;
- provider mode (`recorded` or `live`);
- relevant agent limits and model settings;
- evaluator source hash or eval package hash; and
- Python/package version information needed to reproduce the run.

Refuse baseline-to-candidate comparison when required fingerprint fields differ, unless the report
labels the comparison as intentionally non-equivalent.

#### 9. Reports are printed but not persisted as review artifacts

Write a machine-readable JSON report and a compact Markdown summary for each run. Include:

- fingerprint;
- aggregate dashboard by dimension and category;
- per-case, per-repeat verdicts;
- pass@k, pass^k, and flakiness gap;
- token/tool/latency totals;
- confident-wrong versus honest-unavailable breakdown;
- failed checkpoint and trace evidence;
- baseline deltas; and
- the highest-cost and least-reliable cases.

Suggested paths:

```text
backend/evals/results/<timestamp>-<short-fingerprint>.json
backend/evals/results/<timestamp>-<short-fingerprint>.md
backend/evals/baselines/take_home_baseline.json
```

Generated timestamped runs may be gitignored. Commit one intentionally selected baseline and a
short README explaining how it was produced.

### P2: high-value robustness and reviewer polish

#### 10. No fault-injection evals

Add provider doubles that can deterministically:

- return an empty flight result;
- fail the first flight call and succeed on retry;
- return an empty web result;
- fail the first web call and succeed on the next broad query;
- return prompt-injection text inside a web result; and
- return valid URLs whose content does not support the proposed activity.

Score recovery checkpoints rather than only final pass/fail:

1. detected empty/error result;
2. did not fabricate data;
3. retried only when useful and within limits;
4. used a valid alternative result or returned an honest unavailable state; and
5. still produced a valid grounded itinerary when recovery was possible.

#### 11. Long-horizon behavior is not checkpointed

For a normal trip-planning run, report partial credit/checkpoints for:

1. correct flight search arguments;
2. cheapest offers deterministically identified by the system layer;
3. one to three broad destination searches;
4. grounded activities selected;
5. age/fitness pacing applied;
6. complete day sequence returned; and
7. no booking mutation before human confirmation.

This makes “wrong itinerary” actionable: the report can say whether research, grounding, pacing,
or output assembly failed.

#### 12. UI quality is currently outside the evidence package

The phrase “visually pleasing” is subjective, so do not pretend one LLM score proves it. Provide a
small reviewer checklist and automate objective pieces:

- cheapest offer is visually distinct;
- price, carrier, stops, and departure/arrival are scannable;
- keyboard focus and labels work for form inputs, filters, offer radio buttons, confirm, and
  execute actions;
- loading, unavailable, validation, and provider-error states are visible;
- confirmation and execution are separate, explicit actions;
- layout works at representative mobile and desktop widths;
- activity sources are clickable and clearly associated with activities; and
- the execution panel visibly explains tool calls, timing, and usage.

Capture final desktop/mobile screenshots for the take-home submission and perform a short manual
visual review. Add browser automation only for stable objective assertions.

## Proposed dataset

Start with 12–16 sharp cases rather than hundreds. Tag every case with a category and risk.

### Core repeated baseline (`k=3`)

1. Paris, age 24, low fitness.
2. Paris, age 24, high fitness.
3. Paris, age 78, low fitness.
4. Paris, age 78, high fitness.
5. A second destination/route with moderate fitness to prevent Paris-specific overfitting.
6. A one-way trip to exercise the optional return date path.
7. Complete inputs that must produce an itinerary without unnecessary clarification.
8. A genuinely ambiguous destination prompt that must produce clarification instead of guessing.

### Trajectory and grounding cases

9. Unordered flight fixture with a known computed minimum price.
10. Web results containing one tempting but unsupported activity URL.
11. Web content containing prompt-injection instructions.
12. An itinerary attempt that tries to use a flight as a day activity.

### Failure and recovery cases

13. First web search empty, second broad search succeeds.
14. Flight provider unavailable: no invented offer, clear unavailable evidence.
15. Web provider unavailable: no fabricated activity or citation.
16. First tool result faulted, retry succeeds within the call/token budget.

Keep HITL state cases in `backend/features/booking_hitl.feature`; they already test the correct
system boundary.

## Proposed evaluator inventory

### Deterministic evaluators

Implement these before adding more judge prompts:

- `OutputTypeMatches`
- `CompleteDaySequence`
- `CitationUrlWasReturned`
- `NoFlightAsActivity`
- `SearchFlightsCallCount`
- `SearchFlightsArgumentsMatch`
- `WebSearchCallBudget`
- `NoWebSearchForFlightFacts`
- `NoUnnecessaryClarification`
- `NoHighIntensityForLowFitness`
- `CheapestOfferFirst` for system/provider outputs, not only itinerary output
- `NoBookingMutationInAgentTrace`
- `UsageWithinBudget`
- `HonestUnavailableInsteadOfFabrication`
- `RecoveryCheckpointScore`

Where the output schema permits free-form values, avoid brittle exact-string checks. For example,
the current `intensity: str` allows values such as `strenuous` to evade a check that only rejects
the literal string `high`. An enum would make this enforceable, but adding it is a product contract
change and must follow OpenAPI → Gherkin → red → green.

### Judge-backed evaluators

Use narrow, separately reported rubrics:

- `AgeAndFitnessPacingJudge`
- `ActivitySupportedBySourceJudge`
- `DayPlanCoherenceJudge`
- `ClarificationQualityJudge` for genuinely ambiguous cases only

Each rubric should allow `unknown` when evidence is insufficient. Do not silently turn judge errors
into passes.

## Suggested eval data structures

Replace the prompt-only case input with a structured model so evaluators do not parse important
facts back out of prose with regex.

```python
class EvalTripInput(BaseModel):
    origin: str
    destination: str
    destination_airport: str | None
    depart_date: str
    return_date: str | None
    age: int
    fitness_level: FitnessLevel
    budget_usd: float | None = None

class EvalCaseMetadata(TypedDict):
    category: str
    risk: str
    expects: Literal["itinerary", "clarification", "unavailable"]
    expected_day_count: int | None
    expected_web_search_min: int
    expected_web_search_max: int
    expected_flight_calls: int
```

The task function should build the production prompt through the same production prompt builder or
an extracted shared function. Do not maintain a second prompt format in `evals/run.py`.

## File-by-file implementation plan

### `backend/features/trip_planning.feature`

- Add the red cheapest-order scenario first.
- Add live/cached parity for price ordering.
- If the API contract changes, author `backend/specs/openapi.yaml` before the feature.

### `backend/tests/steps/test_trip_planning.py`

- Seed three deliberately unordered offers.
- Assert ascending prices for live and cached responses.
- Keep the expected minimum computed from seeded offers.

### `backend/app/repositories/trips_repository.py`

- After the red test exists, make all returned offer lists deterministically price-ascending.
- Ensure database cache retrieval has explicit ordering; never rely on database insertion order.
- Use deterministic tie-breakers such as stops, departure time, then stable ID/index.

### `frontend/src/components/FlightSearch.tsx`

- Show a clear `Cheapest` label on the first/lowest-priced visible offer.
- Preserve ordering after stops filtering.
- Add an objective component/browser assertion if the project adds frontend test tooling.

### `backend/evals/dataset.py`

- Introduce structured inputs and richer metadata.
- Expand to the sharp case set above.
- Add category/risk tags.
- Use matched route/date fixtures.
- Add metamorphic groups for age and fitness.

### `backend/evals/evaluators.py`

- Keep deterministic checks code-first.
- Split URL membership from semantic source support.
- Add trajectory, usage, unavailable, day-sequence, and recovery evaluators.
- Replace the broad fitness judge with narrow criteria and document calibration.

### `backend/evals/run.py`

- Use recorded providers by default; add an explicit live-smoke flag.
- Export structured trace attributes, not only URL strings.
- Aggregate repeats into pass@k/pass^k/flakiness metrics.
- Persist JSON and Markdown reports.
- Extend fingerprints and block invalid baseline comparisons.
- Make sure eval trips are cleaned up or clearly isolated if persistent rows would pollute a demo
  database. Prefer a dedicated eval database/schema where practical.

### `backend/evals/fixtures/` or existing recorded fixture area

- Store sanitized real responses for flight and activity providers.
- Include a manifest with capture date, provider, route/query, and content hash.
- Never include API keys or authorization headers.

### `backend/evals/tests/`

Add unit tests for the evaluators themselves. Every evaluator should be shown to:

- pass a known good trace/output;
- fail the exact defect it claims to detect; and
- handle missing/malformed evidence as an explicit failure or unknown, never an accidental pass.

## Take-home requirement coverage matrix

| Requirement | Current evidence | Missing evidence to add |
|---|---|---|
| Use free available APIs | README and architecture name Cerebras, SearchApi, Tavily | Optional live smoke report; keep free-tier limits documented |
| Find cheapest flights | Real flight API and offers are shown | P0 price ordering, cheapest label, deterministic acceptance test |
| Gather activity data and build itinerary | Tavily, structured itinerary, URL grounding | Multiple destinations, complete day sequence, semantic source support |
| Base activities on age and fitness | Two endpoint cases and one LLM judge | Unconfounded factorial cases, metamorphic scores, age-specific checks |
| Ask rather than assume | Structured required intake; union clarification output | Complete-input no-ask case and genuinely ambiguous destination case |
| Explain APIs and AI protocols | `README.md` and `docs/ARCHITECTURE.md` | Submission checklist confirming links/screens are easy to find |
| Visually pleasing UI | Existing React UI | Objective UI checks plus desktop/mobile screenshot review |
| HITL flight booking | Strong state machine and BDD tests | Include a concise passing-test artifact in submission; UI confirmation check |

## Suggested scorecard

Do not collapse everything into one unweighted accuracy number. Show dimensions separately.

| Dimension | Suggested gate |
|---|---|
| Outcome | 100% citation membership; 100% cheapest-order system cases; at least 90% itinerary case pass^3 |
| Trajectory | 100% correct flight arguments; 100% within 1–3 web searches; zero web flight lookups |
| Reliability | Report pass@3 and pass^3; flakiness gap no greater than 0.10 on core cases |
| Safety | Zero invented URLs/offers; zero booking mutation before confirmation; all injection cases contained |
| Cost | No case exceeds configured token/tool limits; report p50/p95 tokens and latency |
| Experience | No unnecessary clarification on complete inputs; all low-fitness cases avoid strenuous plans |

Treat these as initial targets, not claims about the current baseline. Run the baseline first, then
adjust non-safety thresholds only with a documented reason. Safety and take-home compliance gates
should remain hard failures.

## Recommended implementation order

Follow repository SDD+TDD discipline:

1. Add the cheapest-flight Gherkin scenario and red test.
2. Implement deterministic price ordering and the UI cheapest marker.
3. Add evaluator unit tests and the structured trace summary.
4. Move baseline evals to matching recorded real fixtures.
5. Expand the dataset to the factorial/core cases.
6. Add trajectory and output deterministic evaluators.
7. Add repeat aggregation and persisted reports.
8. Add semantic/pacing judges and calibrate them.
9. Add fault injection and checkpoint scoring.
10. Produce one committed baseline report and take-home evidence checklist.

Before committing implementation changes, run the repository-required checks:

```bash
cd backend
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
uv run pytest -q
uv run pyrefly check
```

Then run the deterministic eval baseline with three repeats using the final CLI selected during
implementation. The CLI should default to recorded providers and require an explicit flag for live
API calls.

## Definition of done

This plan is complete when a reviewer can open one Markdown eval report and answer all of these
without reading model transcripts:

- Which offer was cheapest, and was it surfaced first?
- Did the planner call the correct tools with the correct route/dates?
- Were all activities supported by actual research from that run?
- Did age and fitness independently change pacing in the expected direction?
- Did the same case work on every repeat, not only once?
- Were any answers confidently fabricated versus honestly unavailable?
- How many tool calls, tokens, and seconds did each case consume?
- Did injected failures recover without exceeding limits?
- Could the agent mutate a booking without a human confirmation? It must be impossible.
- Is this result directly comparable to the committed baseline?

That is the evidence package the take-home currently lacks.
