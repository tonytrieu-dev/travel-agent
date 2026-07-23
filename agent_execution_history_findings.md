# Agent Execution History Disappears After Hard Refresh

## Summary

The most likely root cause is frontend trip identity loss, not loss of durable backend rows.

`App.tsx` keeps the current `trip` only in React memory:

- `frontend/src/App.tsx:32` initializes `const [trip, setTrip] = useState<TripRequestOut | null>(null)`.
- `frontend/src/App.tsx:51` sets the trip after `createTrip(...)`.
- `frontend/src/App.tsx:196-203` renders `ExecutionPanel` only if `trip` is present.

There is no `localStorage`, URL route parameter, `GET /api/trips/{id}`, or `GET /api/trips` reload path. A hard refresh recreates the React app with `trip === null`, so the UI loses the trip context that tells it which durable history to fetch.

This makes the panel appear non-durable even when the backend rows still exist.

## What I Confirmed

Backend execution history for a specific trip is intended to be durable and multi-run:

- `backend/app/repositories/trips_repository.py:269-305` loads all `AgentRun` rows for the given `trip_id`, ordered newest first, then loads all steps for those runs.
- `backend/tests/test_execution_panel_route.py:71-85` verifies that multiple runs for the same trip are returned newest first.
- I ran `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_execution_panel_route.py` from `backend/`; result: `4 passed in 1.57s`.

Frontend polling only works while the in-memory trip exists:

- `frontend/src/hooks/useTripExecution.ts:28-36` fetches `/trips/{tripId}/execution`.
- `frontend/src/hooks/useTripExecution.ts:38-43` starts polling only when `enabled` is true and `tripId != null`.
- After a hard refresh, there is no persisted `tripId`, so the hook has nothing durable to re-fetch.

API client currently has no trip reload/list endpoint:

- `frontend/src/api/client.ts:43-74` has `createTrip`, `updateTrip`, `searchTripFlights`, `planTrip`, and `getTripExecution`.
- It does not have `getTrip(tripId)` or `listTrips()`.

OpenAPI currently defines `POST /api/trips`, `PATCH /api/trips/{trip_id}`, and `GET /api/trips/{trip_id}/execution`, but not `GET /api/trips/{trip_id}` or `GET /api/trips`.

## Why The User Sees Only Run #10

Two likely scenarios:

1. If run `#10` is an `AgentRun.id`, that id is globally auto-incremented. Seeing only run `#10` can mean the UI is scoped to the current trip, while runs `#9`, `#8`, etc. belong to earlier trips.
2. If the user creates a new trip after the hard refresh, the panel will naturally show only the run associated with that new trip. Earlier durable rows still exist, but they are not reachable because the browser lost the old trip id.

Important distinction: the existing backend route is per trip, not global execution history.

## Secondary Backend Risk To Check

`get_execution_panel` calls:

```python
col(AgentRunStep.agent_run_id).in_([run.id for run in agent_runs])
```

If `agent_runs` is empty, SQLAlchemy usually compiles this safely, and the existing test for an empty panel passes. So this is not the observed issue, but it is worth keeping an eye on if database dialect behavior changes.

## Recommended Fix

Treat the selected trip id as durable UI state and give the frontend a way to recover it.

Suggested contract-first path:

1. Add `GET /api/trips/{trip_id}` to `backend/specs/openapi.yaml`.
2. Implement `repository.get_trip(session, trip_id)` and `GET /api/trips/{trip_id}` in `backend/app/routes/trips.py`.
3. Add frontend `getTrip(tripId)` in `frontend/src/api/client.ts`.
4. Persist the active trip id in the browser, for example `localStorage.setItem("travel-agent.activeTripId", String(trip.id))` after create/update.
5. On app mount, read that id and call `getTrip(id)`. If it 404s, clear the stored id.
6. Keep `ExecutionPanel` fetching `/api/trips/{tripId}/execution` with the recovered id.

Better product fix:

Add `GET /api/trips` newest-first for the current user and render a trip picker/sidebar. Then a hard refresh can restore the most recent trip and also let the user intentionally select older trips. This matches the user expectation that prior runs remain visible and auditable.

## Tests Claude Code Should Add

Backend:

- Contract test update for `GET /api/trips/{trip_id}` and/or `GET /api/trips`.
- Route test: `GET /api/trips/{trip_id}` returns the persisted trip after a new session/request.
- Route test: `GET /api/trips` returns persisted trips newest first for the current user.

Frontend:

- Unit/component test or Playwright test that creates a trip, records active trip id, remounts/reloads the app, and verifies `ExecutionPanel` fetches the same trip's execution history.
- Test 404 recovery: stored trip id points to a missing trip, app clears it and shows the create-trip state.

## Files Most Relevant For The Fix

- `backend/specs/openapi.yaml`
- `backend/app/routes/trips.py`
- `backend/app/repositories/trips_repository.py`
- `backend/app/schemas.py`
- `backend/tests/test_execution_panel_route.py`
- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/api/types.ts`
- `frontend/src/hooks/useTripExecution.ts`
- `frontend/src/components/ExecutionPanel.tsx`

## Current Worktree Note

The repo already had many modified files before any fix was made. I did not revert or overwrite unrelated changes. I only investigated and wrote this handoff note.
