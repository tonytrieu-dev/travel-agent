# Travel Agent

An AI travel-planning agent: give it an origin, destination, dates, age, and fitness level, and
it searches real flights, researches real activities, and builds a fitness-tailored day-by-day
itinerary. Required fields are validated at intake so the agent never guesses at missing trip
data; it only asks a clarifying question when a *provided* value is genuinely ambiguous (e.g. a
destination name that could mean more than one place). A strict **human-in-the-loop** gate sits
between any plan and any booking action: the agent can research and propose flights but has no
booking capability of its own — a person must explicitly review and approve before anything
moves forward.

## What it does

1. **Plan a trip** — origin, destination, dates, age, and fitness level are all required at
   intake, so the agent always has what it needs to pace the itinerary without guessing. It still
   asks a clarifying question if a provided value is genuinely ambiguous.
2. **Search real flights** — Google Flights results via SearchApi.io, cached by route+date to
   protect a one-time search quota.
3. **Get a real itinerary** — the agent researches activities via Tavily web search and returns
   a day-by-day plan where every activity cites the real source URL it came from. No invented
   activities, no fabricated data.
4. **Human-approved booking handoff** — review a proposed flight, explicitly approve it, then
   retrieve real booking options, as three separate steps. The agent never books anything itself;
   approval only unlocks a deterministic, audited workflow that hands off to the airline/OTA for
   the actual purchase. A 30-minute price-staleness window expires stale requests automatically.
5. **Watch the agent work** — an execution panel shows the run's tool calls, token usage,
   context-budget utilization, and timing, live.

## Stack

- **Backend:** FastAPI, Pydantic AI, SQLModel/asyncpg, PostgreSQL 16, Alembic, DBOS (durable
  workflow execution, reuses the same Postgres instance).
- **LLM:** Cerebras `gpt-oss-120b` via Pydantic AI.
- **Flights:** SearchApi.io Google Flights (structured JSON; free tier at signup time, see
  [searchapi.io/pricing](https://www.searchapi.io/pricing)).
- **Activities:** Tavily web search (free tier at signup time, see
  [tavily.com/#pricing](https://www.tavily.com/#pricing)).
- **Frontend:** React 19 + Vite + Tailwind CSS v4, TypeScript. A structured trip form drives the
  agent; a live activity feed streams its tool calls inline on the trip page, and a separate
  execution panel (with a live indicator in the sidebar nav) shows the full run trace.
- **Evals:** `pydantic-evals` — deterministic + LLM-judged scoring of agent quality, separate
  from the pytest suite that gates system correctness.

All three external services offer a free tier as of this writing; check each provider's current
pricing page before relying on exact quota numbers, which change over time.

## Running it

### 1. Database

Either Docker or a local Postgres install works.

```bash
# Docker
docker compose up -d
```

or, if you'd rather run Postgres natively (e.g. via Homebrew on macOS), just make sure a
`travel_agent` database exists and matches the `DATABASE_URL` you set in `.env` below.

### 2. Environment

```bash
cp .env.example .env
```

Fill in `CEREBRAS_API_KEY`, `SEARCHAPI_API_KEY`, and `TAVILY_API_KEY` (links to get each one are
in the file's comments). Adjust `DATABASE_URL` if you're not using the Docker default.

### 3. Backend

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Backend serves on `http://localhost:8000`; interactive docs at `/docs`.

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend serves on `http://localhost:5173` (already whitelisted by the backend's CORS config).

### 5. Tests

```bash
cd backend
uv run pytest -q
uv run pyrefly check
```

### 6. Evals

```bash
cd backend
uv run python -m evals.run --repeat 3
```

Scores the agent (not just the system) against a small dataset: are itineraries fitness-appropriate
(scored by an LLM judge, on top of the deterministic `reject_unsafe_intensity` guardrail every
real run also goes through), and are all cited activities grounded in real search results. Runs
against the real Cerebras API, so it spends real quota — mind your request limits before running
repeatedly.

## Key decisions

- **HITL is a REST state machine, not an agent tool.** Booking moves through
  `PENDING_USER_CONFIRMATION → CONFIRMED → EXECUTED` (or `CANCELLED`/`EXPIRED`) driven entirely
  by explicit human clicks against `/bookings/*` routes. The agent can plan and search but never
  writes a booking — this makes "a human must click before the write" a structural guarantee,
  not a prompt-dependent one.
- **Ask, don't assume.** The planner's output type is a union of `Itinerary | ClarificationOut`;
  missing trip details produce real clarifying questions instead of a guessed itinerary.
- **Real data only, honestly degraded.** Flight and activity adapters never fabricate results.
  On a quota/rate-limit/empty response they return cached real data if available, or an honest
  "unavailable" reason — never invented offers or activities.
- **Durable execution via DBOS.** The planner and booking-execution flows are wrapped as DBOS
  workflows so a crash mid-run resumes rather than silently losing state, reusing the same
  Postgres instance (no extra infrastructure).
- **Append-only audit trail enforced at the database.** Booking transitions and execution events
  are protected by DB triggers that reject `UPDATE`/`DELETE`, not just application-level
  convention.
- **Rate limiting protects scarce third-party quota.** Per-IP request caps and a global
  concurrency cap on `/plan` and `/flights/search` keep the app from burning through LLM
  request limits or SearchApi's one-time search allotment.
