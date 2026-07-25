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
   retrieve real checkout links, as three separate steps. Nothing here books a flight. Approval
   unlocks a deterministic, audited workflow whose output is airline/OTA checkout links with the
   chosen flight already attached; you complete the purchase on the carrier's own site, and the
   reference this app stores is its own audit id, not an airline confirmation number. Because no
   fare is actually held, a 30-minute freshness window guards against handing you a stale price:
   approving or executing past it marks the booking `EXPIRED` and asks you to search again.
5. **Watch the agent work** — an execution panel shows every run's tool calls, token usage,
   context-budget utilization, and timing, live. It's global across all of your trips (filterable
   by route and status), not just the one you're currently planning.
6. **Revisit any past trip** — a "Your trips" tab lists everything you've created, newest first
   and filterable by date range, so a trip never disappears just because a newer one replaced it
   as the active one in the planner.

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
  execution panel shows the full run trace across every trip. A "Your trips" tab lists every trip
  you've created, so switching or revisiting one never loses its history.
- **Evals:** `pydantic-evals` — deterministic scoring by default, with optional LLM-judged
  fitness-appropriateness scoring, separate from the pytest suite that gates system correctness.

All three external services offer a free tier as of this writing; check each provider's current
pricing page before relying on exact quota numbers, which change over time. For why each one was
picked over its alternatives, see [DECISIONS.md](docs/DECISIONS.md).

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
# Optional Gemini judge:
uv run python -m evals.run --with-judge
```

Scores the agent (not just the system) against a small dataset: are cited activities grounded in
real search results, are flights searched exactly once, and is the itinerary safe for the
traveler? The default suite is deterministic; `--with-judge` opts into the Gemini
`FitnessAppropriateness` evaluator. Runs against the real Cerebras API, so it spends real quota.

### 7. (Optional) Slack human-in-the-loop approvals

Booking approval can be routed through Slack instead of the in-app UI. Slack's Interactivity
callback needs a public HTTPS URL, so local development requires a tunnel:

```bash
ngrok http 8000
```

Use the printed `https://*.ngrok.io` URL as the Slack app's Request URL. Full setup steps
(app creation, tokens, channel config) are in [`docs/SLACK_SETUP.md`](docs/SLACK_SETUP.md).
Nothing here is required for the app to run — without it, the Connectors tab shows the Slack
toggle greyed out and booking approval stays in-app.

## Key decisions

Every load-bearing choice in this project — HITL as a REST state machine, ask-don't-assume typing,
real-data-only degradation, DBOS for durable execution, the append-only audit trail, rate limiting,
and why each external API was picked over its alternatives — is documented with its reasoning in
[docs/DECISIONS.md](docs/DECISIONS.md).
