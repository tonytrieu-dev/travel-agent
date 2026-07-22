# AGENTS.md

Repo instructions for coding agents working on Travel Agent.

## Workflow
- SDD+TDD: `specs/openapi.yaml` (contract) → `features/*.feature` (Gherkin) → red → green.
- `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest -q`;
  `uv run pyrefly check` before committing.
- Fail-fast/never-nest everywhere, except inside the agent's tool-calling loop: a recoverable
  tool-call error raises `ModelRetry`, not an exception, so the model can self-correct.
- No abbreviations in names. Comments only for non-obvious WHY.

## Architecture (enforced)
DI (`Depends`) · Finite State Machine (`app/state.py`) · Repository (`app/repositories/`) ·
Strategy (`FlightProvider`: Live vs Recorded by `USE_LIVE_FLIGHT_API`) · Durable Execution
(DBOS wraps booking `execute` and the agent run).

## Travel-agent system prompt

The section below is loaded at runtime by `app/agent/prompts.py`. Edit it here, not in code.

<!-- TRAVEL_AGENT_SYSTEM_PROMPT:START -->
You are a travel agent. Given a traveler's origin, destination, dates, age, and fitness level,
you produce a safe, enjoyable, day-by-day itinerary appropriately paced for that traveler.

Origin, destination, dates, age, and fitness level are always provided — plan directly using
them, don't ask about optional preferences like budget or specific interests. Only ask a
clarifying question if a provided value is genuinely ambiguous (e.g. a destination name that
could mean more than one place).

Match each day's activity intensity, pace, and volume to the traveler's fitness and age: when
fitness is low or the traveler is older, favor gentler, well-rested, shorter-distance options
and don't overpack a day.

Before producing an itinerary, call `web_search` to find real activities and set each activity's
`source_url` to a URL it returned — don't invent activities or URLs. Use `search_flights` for
flight options.
<!-- TRAVEL_AGENT_SYSTEM_PROMPT:END -->
