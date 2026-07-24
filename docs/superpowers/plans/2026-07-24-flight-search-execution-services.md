# FlightSearchService + ExecutionService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the flight-search business logic currently split/duplicated between `routes/trips.py`, `repositories/trips_repository.py`, and `agent/planner.py` into one `FlightSearchService`, and extract the execution-run lifecycle currently spread across `execution_log.py`/`observability.py`/`dbos_runtime.py` into one `ExecutionService`, without changing any observable behavior, API shape, DB schema, or DBOS workflow signature.

**Architecture:** Two deep modules behind existing call sites. `FlightSearchService` (new `backend/app/services/flight_search.py`) owns trip lookup, same-trip TTL cache, cross-trip cache, round-trip completeness filtering, provider invocation, persistence, cheapest-first ordering, and `search_flights` event/step logging — parameterized by `persist`/`allow_cross_trip_cache` so it reproduces the route's full-persistence behavior and the planner tool's read-mostly behavior from one implementation. `ExecutionService` (new class inside `backend/app/agent/execution_log.py`) wraps the existing `execution_context()` async generator and `observability.persist_agent_run()` behind a `start_run()`/`ExecutionRun` object, while leaving the ContextVar, locking, and module-level `current_trip()`/`record_event()` functions in place as compatibility shims so nothing else in the codebase needs to change.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, pydantic_ai, DBOS, pytest, pyrefly.

## Global Constraints

- Do not touch: booking/HITL code, frontend components, OpenAPI schemas, DBOS workflow signatures, provider adapters (`FlightProvider`/`LiveSearchApiProvider`/`RecordedProvider`), DB schema/migrations.
- Preserve exactly: cheapest-first ordering, same-trip TTL caching + idempotent searches, cross-trip identical-search cache reuse, round-trip completeness filtering, honest unavailable-provider results, `search_flights` execution events + agent-run steps, partial failed-run persistence, existing REST response shapes (`FlightSearchOut`/`FlightOfferOut`/`FlightLegOut`/`ExecutionPanelOut`), human confirmation staying outside the agent.
- Trust boundary: `planner.search_flights` validates model-supplied IATA args for shape only; the actual search always uses the trip's stored `origin`/`destination_airport`/`depart_date`/`return_date`.
- Test monkeypatch surface: `tests/conftest.py`'s `client` fixture patches `app.routes.trips.get_flight_provider` — that import must stay in `routes/trips.py`.
- 404 ordering: a search against a nonexistent trip must return before any `AgentRun` row is created.
- Tests-first: write/adjust the test before the implementation in every task below.

---

## File Structure

- **Create** `backend/app/services/__init__.py` — empty, marks the new package.
- **Create** `backend/app/services/flight_search.py` — `FlightSearchService`, `TripFlightSearchOutcome`. Owns everything currently in `trips_repository.py`'s `search_flights`/`get_recent_flight_results`/`_record_search_flights_run`/`_to_flight_result`/`_complete_flight_results`/`_cheapest_first`/`offer_summary`/`flight_provider_name`.
- **Modify** `backend/app/repositories/trips_repository.py` — delete the functions moved into the service; `get_trip_snapshot` keeps its own `_cheapest_first` (still needed there, kept local) since it doesn't do provider search.
- **Modify** `backend/app/routes/trips.py` — `search_trip_flights` builds a `FlightSearchService` and calls `.search(trip_id, agent_run=agent_run)`.
- **Modify** `backend/app/agent/planner.py` — `search_flights` tool builds a `FlightSearchService` and calls `.search(trip_id, persist=False, allow_cross_trip_cache=False)`.
- **Create** `backend/tests/test_flight_search_service.py` — boundary tests for the new service.
- **Modify** `backend/tests/test_planner_search_flights.py` — keep as regression coverage for the planner tool's public dict shape (thin now that logic lives in the service).
- **Modify** `backend/app/agent/execution_log.py` — add `ExecutionService`/`ExecutionRun` wrapping the existing `execution_context`/`current_trip`/`record_event`, which remain as-is.
- **Modify** `backend/app/dbos_runtime.py` — `_run_planner_workflow` uses `ExecutionService(session).start_run(...)` instead of calling `execution_context` directly.
- **Create** `backend/tests/test_execution_service.py` — boundary tests for `ExecutionService`.

---

## Task 1: FlightSearchService skeleton + missing-trip behavior

**Files:**
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/flight_search.py`
- Test: `backend/tests/test_flight_search_service.py`

**Interfaces:**
- Produces: `TripFlightSearchOutcome(offers: list[FlightSearchResult], unavailable_reason: str | None, source: str)`, `FlightSearchService(session: AsyncSession, provider: FlightProvider)` with `async def search(self, trip_id: int, *, agent_run: AgentRun | None = None, persist: bool = True, allow_cross_trip_cache: bool = True) -> TripFlightSearchOutcome`. Raises `TripError` (from `app.repositories.trips_repository`) on missing trip — same exception type callers already catch/propagate.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_flight_search_service.py
"""Boundary tests for FlightSearchService: the single implementation behind both the
/flights/search route (full persistence + cross-trip cache) and the planner's search_flights
tool (same-trip cache + live search only, never persists, never reaches across trips)."""

import pytest

from app.adapters.flights_searchapi import NormalizedFlightOffer
from app.repositories.trips_repository import TripError
from app.services.flight_search import FlightSearchService
from tests.conftest import FlightSearchSpy
from tests.db_helpers import run_db, seed_trip


def test_missing_trip_raises_trip_error() -> None:
    async def _work(session):
        service = FlightSearchService(session, FlightSearchSpy())
        with pytest.raises(TripError) as excinfo:
            await service.search(999_999)
        return excinfo.value

    error = run_db(_work)
    assert error.status_code == 404
    assert error.code.value == "trip_not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/__init__.py
```

```python
# backend/app/services/flight_search.py
"""FlightSearchService: the one implementation of flight-offer search, caching, filtering,
persistence, and ordering behind both the /flights/search route and the planner's
search_flights tool. The two callers differ only in persist/allow_cross_trip_cache — the
route persists and reaches across trips, the planner tool trusts only this trip's own
recent search and never writes offers (see planner.search_flights for why)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.adapters.flights_searchapi import FlightProvider, NormalizedFlightOffer
from app.agent.execution_log import record_event
from app.config import FLIGHT_CACHE_TTL_MINUTES
from app.models import (
    AgentRun,
    AgentRunStep,
    AgentStepKind,
    ExecutionEventKind,
    FlightResultSource,
    FlightSearchResult,
    TripRequest,
    TripStatus,
    utcnow,
)
from app.repositories.trips_repository import ErrorCode, TripError


@dataclass
class TripFlightSearchOutcome:
    offers: list[FlightSearchResult]
    unavailable_reason: str | None
    source: str  # "same_trip_cache" | "cross_trip_cache" | "live"


class FlightSearchService:
    def __init__(self, session: AsyncSession, provider: FlightProvider) -> None:
        self._session = session
        self._provider = provider

    async def search(
        self,
        trip_id: int,
        *,
        agent_run: AgentRun | None = None,
        persist: bool = True,
        allow_cross_trip_cache: bool = True,
    ) -> TripFlightSearchOutcome:
        trip = await self._session.get(TripRequest, trip_id)
        if trip is None:
            raise TripError(ErrorCode.TRIP_NOT_FOUND, 404, f"No trip {trip_id}.")
        # Captured once per call so every branch (cache hit, cross-trip cache, live) can
        # finalize agent_run.started_at/total_ms consistently with the pre-refactor
        # _record_search_flights_run, which timed the whole call, not just the provider hop.
        run_started_at = utcnow()
        run_started_monotonic = time.monotonic()
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/__init__.py backend/app/services/flight_search.py backend/tests/test_flight_search_service.py
git commit -m "feat: add FlightSearchService skeleton with missing-trip behavior"
```

---

## Task 2: Same-trip TTL cache reuse (no provider call, no new rows)

**Files:**
- Modify: `backend/app/services/flight_search.py`
- Test: `backend/tests/test_flight_search_service.py`

**Interfaces:**
- Consumes: `_cheapest_first`, `_complete_flight_results` (module-private helpers, moved here from `trips_repository.py` this task).

- [ ] **Step 1: Write the failing test**

```python
def test_same_trip_cache_hit_reuses_results_without_calling_provider() -> None:
    async def _work(session):
        from tests.db_helpers import seed_flight_search_results

        trip_id = await seed_trip(session)
        await seed_flight_search_results(session, trip_id)  # carrier="AF" by default
        provider = FlightSearchSpy(
            offers=[
                NormalizedFlightOffer(
                    carrier="LIVE", price_usd=1.0, currency="USD",
                    depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:00:00",
                    stops=0, booking_token="tok", raw_offer={},
                )
            ]
        )
        service = FlightSearchService(session, provider)
        outcome = await service.search(trip_id)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert provider.calls == 0, "a same-trip cache hit must never call the provider"
    assert outcome.source == "same_trip_cache"
    assert outcome.offers[0].carrier == "AF"
    assert outcome.unavailable_reason is None


def test_cache_hit_preserves_agent_run_timing_metadata() -> None:
    """The pre-refactor _record_search_flights_run set agent_run.started_at/finished_at/
    total_ms on every branch, not just live search — the execution panel's per-run duration
    depends on this even for a cache-hit run. A regression here would silently zero out
    duration for the common case (repeat searches within the TTL)."""

    async def _work(session):
        from app.models import AgentRun
        from tests.db_helpers import seed_flight_search_results

        trip_id = await seed_trip(session)
        await seed_flight_search_results(session, trip_id)
        agent_run = AgentRun(trip_request_id=trip_id, status="running", model="test-model")
        session.add(agent_run)
        await session.commit()
        service = FlightSearchService(session, FlightSearchSpy())
        await service.search(trip_id, agent_run=agent_run)
        return agent_run

    agent_run = run_db(_work)
    assert agent_run.status == "completed"
    assert agent_run.started_at is not None
    assert agent_run.finished_at is not None
    assert agent_run.total_ms is not None and agent_run.total_ms >= 0, (
        f"a cache-hit run must still report a real elapsed duration, got {agent_run.total_ms}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/services/flight_search.py` (replacing the `raise NotImplementedError` body and adding the private helpers, copied verbatim from `trips_repository.py`):

```python
def _cheapest_first(offers: list[FlightSearchResult]) -> list[FlightSearchResult]:
    """Ascending price order is a backend guarantee, held on every path (fresh live search,
    own-trip reuse, cross-trip cache) so all three agree — the frontend lists offers in the
    order the API returns them."""
    return sorted(offers, key=lambda offer: offer.price_usd)


def _complete_flight_results(
    offers: list[FlightSearchResult], return_date: str | None
) -> list[FlightSearchResult]:
    if return_date is None:
        return offers
    return [
        offer
        for offer in offers
        if offer.raw_offer.get("return_flights")
        and offer.raw_offer.get("booking_token") == offer.booking_token
    ]


def offer_summary(offer: FlightSearchResult | NormalizedFlightOffer) -> dict:
    return {
        "carrier": offer.carrier,
        "price_usd": offer.price_usd,
        "currency": offer.currency,
        "depart_at": offer.depart_at,
        "arrive_at": offer.arrive_at,
        "stops": offer.stops,
    }


def flight_provider_name(provider: FlightProvider) -> str:
    return {
        "LiveSearchApiProvider": "SearchApi",
        "RecordedProvider": "Recorded flights",
    }.get(type(provider).__name__, type(provider).__name__)
```

Inside `FlightSearchService`, add:

```python
    async def _get_same_trip_cache(self, trip: TripRequest) -> list[FlightSearchResult]:
        cutoff = utcnow() - timedelta(minutes=FLIGHT_CACHE_TTL_MINUTES)
        results = list(
            await self._session.scalars(
                select(FlightSearchResult).where(
                    col(FlightSearchResult.trip_request_id) == trip.id,
                    col(FlightSearchResult.created_at) >= cutoff,
                )
            )
        )
        return _cheapest_first(_complete_flight_results(results, trip.return_date))
```

Replace the `search` body:

```python
        same_trip_cache = await self._get_same_trip_cache(trip)
        if same_trip_cache:
            await self._log(
                trip, agent_run, "ok",
                f"{len(same_trip_cache)} offers (reused, already searched within TTL)",
                same_trip_cache, run_started_at, run_started_monotonic,
            )
            return TripFlightSearchOutcome(same_trip_cache, None, "same_trip_cache")
        raise NotImplementedError
```

```python
    async def _log(
        self,
        trip: TripRequest,
        agent_run: AgentRun | None,
        status: str,
        detail: str,
        offers: list[FlightSearchResult] | list[NormalizedFlightOffer],
        run_started_at: datetime,
        run_started_monotonic: float,
        duration_ms: int | None = None,
    ) -> None:
        await record_event(
            ExecutionEventKind.API_CALL, "search_flights", status, detail, duration_ms,
            data={"offers": [offer_summary(offer) for offer in offers]},
            provider=agent_run.model if agent_run is not None else flight_provider_name(self._provider),
        )
        if agent_run is None:
            return
        finished_at = utcnow()
        total_ms = round((time.monotonic() - run_started_monotonic) * 1000)
        assert agent_run.id is not None
        agent_run.status = "completed" if status == "ok" else status
        agent_run.started_at = run_started_at
        agent_run.finished_at = finished_at
        agent_run.total_ms = total_ms
        self._session.add(
            AgentRunStep(
                agent_run_id=agent_run.id, seq=1, kind=AgentStepKind.TOOL, name="search_flights",
                status=status, duration_ms=duration_ms if duration_ms is not None else total_ms,
                input_summary=(
                    f"{trip.origin} to {trip.destination_airport}, "
                    f"{trip.depart_date} to {trip.return_date or 'one-way'}"
                ),
                output_summary=detail, tokens=0,
            )
        )
        await self._session.commit()
```

This reproduces `_record_search_flights_run` exactly: `agent_run.started_at`/`total_ms` are set from the call's real start (`run_started_at`/`run_started_monotonic`, captured once in `search()` before any branch runs — see Task 1), not left at their pre-call defaults, on every branch including a same-trip cache hit. `AgentRunStep.duration_ms` falls back to `total_ms` when no `duration_ms` (i.e. no provider timing) is available, matching the original's `provider_duration_ms if provider_duration_ms is not None else total_ms`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/flight_search.py backend/tests/test_flight_search_service.py
git commit -m "feat: same-trip TTL cache reuse in FlightSearchService"
```

---

## Task 3: Live search + persistence + cheapest-first + unavailable reason

**Files:**
- Modify: `backend/app/services/flight_search.py`
- Test: `backend/tests/test_flight_search_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_expired_cache_invokes_provider_and_persists_cheapest_first() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        provider = FlightSearchSpy(
            offers=[
                NormalizedFlightOffer(
                    carrier="EXPENSIVE", price_usd=900.0, currency="USD",
                    depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:00:00",
                    stops=0, booking_token="tok-a", raw_offer={},
                ),
                NormalizedFlightOffer(
                    carrier="CHEAP", price_usd=100.0, currency="USD",
                    depart_at="2026-08-01T10:00:00", arrive_at="2026-08-01T22:00:00",
                    stops=1, booking_token="tok-b", raw_offer={},
                ),
            ]
        )
        service = FlightSearchService(session, provider)
        outcome = await service.search(trip_id)
        from tests.db_helpers import get_flight_search_results

        persisted = await get_flight_search_results(session, trip_id)
        return provider, outcome, persisted

    provider, outcome, persisted = run_db(_work)
    assert provider.calls == 1
    assert outcome.source == "live"
    assert [offer.carrier for offer in outcome.offers] == ["CHEAP", "EXPENSIVE"], (
        "results must be cheapest-first regardless of provider order"
    )
    assert len(persisted) == 2, "live search results must be persisted"


def test_unavailable_provider_result_is_reported_honestly() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        provider = FlightSearchSpy(offers=[], unavailable_reason="No cassette for this route")
        service = FlightSearchService(session, provider)
        return await service.search(trip_id)

    outcome = run_db(_work)
    assert outcome.unavailable_reason == "No cassette for this route"
    assert outcome.offers == []


def test_provider_is_called_with_the_stored_trips_criteria_not_anything_else() -> None:
    """FlightSearchService.search takes no route/date arguments beyond trip_id — this pins
    that guarantee at the service level (not just the old planner-tool call site): whatever
    criteria the trip was created with is what reaches the provider, always, since there is
    no parameter through which a caller could redirect the search."""

    async def _work(session):
        trip_id = await seed_trip(session)  # db_helpers default: JFK -> CDG, 2026-08-01, one-way
        provider = FlightSearchSpy()
        service = FlightSearchService(session, provider)
        await service.search(trip_id)
        return provider

    provider = run_db(_work)
    assert provider.last_search_params == {
        "departure_id": "JFK",
        "arrival_id": "CDG",
        "outbound_date": "2026-08-01",
        "return_date": None,
    }, f"provider must be called with the trip's own stored criteria, got {provider.last_search_params}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

Replace the trailing `raise NotImplementedError` in `search` with the live-search branch (cross-trip cache check comes in Task 4 and slots in before this):

```python
        provider_started = time.monotonic()
        outcome = await self._provider.search_offers(
            trip.origin, trip.destination_airport, trip.depart_date, trip.return_date
        )
        provider_duration_ms = round((time.monotonic() - provider_started) * 1000)
        results = [
            FlightSearchResult(
                trip_request_id=trip_id, offer_index=index, carrier=offer.carrier,
                price_usd=offer.price_usd, currency=offer.currency, depart_at=offer.depart_at,
                arrive_at=offer.arrive_at, stops=offer.stops, booking_token=offer.booking_token,
                raw_offer=offer.raw_offer, source=FlightResultSource.LIVE,
            )
            for index, offer in enumerate(outcome.offers)
        ]
        if persist:
            self._session.add_all(results)
            if results:
                trip.status = TripStatus.FLIGHTS_SEARCHED
            await self._session.commit()
        await self._log(
            trip, agent_run,
            "ok" if outcome.unavailable_reason is None else "unavailable",
            f"{len(results)} offers" if outcome.unavailable_reason is None else outcome.unavailable_reason,
            outcome.offers if not persist else results,
            run_started_at, run_started_monotonic,
            provider_duration_ms,
        )
        return TripFlightSearchOutcome(_cheapest_first(results), outcome.unavailable_reason, "live")
```

`trip_id` here is the parameter already in scope from the method signature (Task 1). `persist` gates whether rows land in the DB — the route's default `persist=True` writes; the planner-tool call in Task 6 passes `persist=False`, so `results` are still built (for the return value/ordering) but never added to the session or committed. Pass `outcome.offers if not persist else results` to `_log` because `offer_summary` accepts either `FlightSearchResult` or `NormalizedFlightOffer`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/flight_search.py backend/tests/test_flight_search_service.py
git commit -m "feat: live search, persistence, cheapest-first ordering in FlightSearchService"
```

---

## Task 4: Cross-trip identical-search cache reuse + round-trip completeness filtering

**Files:**
- Modify: `backend/app/services/flight_search.py`
- Test: `backend/tests/test_flight_search_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_cross_trip_cache_reuses_identical_route_without_calling_provider() -> None:
    async def _work(session):
        from tests.db_helpers import seed_flight_search_results

        source_trip_id = await seed_trip(session)
        await seed_flight_search_results(session, source_trip_id)
        other_trip_id = await seed_trip(session)  # same default JFK->CDG route/dates
        provider = FlightSearchSpy()
        service = FlightSearchService(session, provider)
        outcome = await service.search(other_trip_id)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert provider.calls == 0
    assert outcome.source == "cross_trip_cache"
    assert outcome.offers[0].carrier == "AF"


def test_cross_trip_cache_disabled_for_planner_tool_falls_through_to_live() -> None:
    async def _work(session):
        from tests.db_helpers import seed_flight_search_results

        source_trip_id = await seed_trip(session)
        await seed_flight_search_results(session, source_trip_id)
        other_trip_id = await seed_trip(session)
        provider = FlightSearchSpy()
        service = FlightSearchService(session, provider)
        outcome = await service.search(other_trip_id, persist=False, allow_cross_trip_cache=False)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert provider.calls == 1, "planner-tool searches must never reach across trips"
    assert outcome.source == "live"


def test_incomplete_round_trip_offers_are_filtered_out_of_cache_reads() -> None:
    async def _work(session):
        from app.models import FlightSearchResult

        trip_id = await seed_trip(session, return_date="2026-08-08")
        session.add(
            FlightSearchResult(
                trip_request_id=trip_id, offer_index=0, carrier="INCOMPLETE", price_usd=50.0,
                currency="USD", depart_at="2026-08-01T09:00:00", arrive_at="2026-08-01T21:00:00",
                stops=0, booking_token="tok", raw_offer={},  # no return_flights paired
            )
        )
        await session.commit()
        # offers=[] so a fall-through to live search is unambiguous: if the filter failed to
        # drop the incomplete cached offer, outcome.offers would be non-empty here.
        provider = FlightSearchSpy(offers=[])
        service = FlightSearchService(session, provider)
        outcome = await service.search(trip_id)
        return provider, outcome

    provider, outcome = run_db(_work)
    assert outcome.offers == [], "an unpaired round-trip offer must never be served from cache"
    assert outcome.source == "live", "filtering the incomplete cached offer forces a live search"
    assert provider.calls == 1, (
        "the incomplete cached offer must not short-circuit the search — the provider must "
        "actually be invoked, not just happen to return an empty result by coincidence"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: FAIL — cross-trip tests get `source == "live"` unexpectedly (no cross-trip lookup exists yet); round-trip test currently passes already via `_complete_flight_results` reused in Task 2's `_get_same_trip_cache` — verify it passes already, keep it as a regression pin.

- [ ] **Step 3: Write minimal implementation**

Insert between the same-trip-cache check and the live-search branch in `search`:

```python
        if allow_cross_trip_cache:
            cross_trip_offers = await self._get_cross_trip_cache(trip)
            if cross_trip_offers:
                results = [
                    FlightSearchResult(
                        trip_request_id=trip_id, offer_index=source_offer.offer_index,
                        carrier=source_offer.carrier, price_usd=source_offer.price_usd,
                        currency=source_offer.currency, depart_at=source_offer.depart_at,
                        arrive_at=source_offer.arrive_at, stops=source_offer.stops,
                        booking_token=source_offer.booking_token, raw_offer=source_offer.raw_offer,
                        source=FlightResultSource.CACHED,
                    )
                    for source_offer in cross_trip_offers
                ]
                self._session.add_all(results)
                trip.status = TripStatus.FLIGHTS_SEARCHED
                await self._session.commit()
                await self._log(
                    trip, agent_run, "ok",
                    f"{len(results)} offers (cached from an identical route/date search)",
                    results, run_started_at, run_started_monotonic,
                )
                return TripFlightSearchOutcome(_cheapest_first(results), None, "cross_trip_cache")
```

Add the helper method:

```python
    async def _get_cross_trip_cache(self, trip: TripRequest) -> list[FlightSearchResult]:
        cutoff = utcnow() - timedelta(minutes=FLIGHT_CACHE_TTL_MINUTES)
        source_trip_id = await self._session.scalar(
            select(FlightSearchResult.trip_request_id)
            .join(TripRequest, col(FlightSearchResult.trip_request_id) == col(TripRequest.id))
            .where(
                col(TripRequest.origin) == trip.origin,
                col(TripRequest.destination_airport) == trip.destination_airport,
                col(TripRequest.depart_date) == trip.depart_date,
                col(TripRequest.return_date) == trip.return_date,
                col(FlightSearchResult.created_at) >= cutoff,
            )
            .order_by(col(FlightSearchResult.created_at).desc())
            .limit(1)
        )
        if source_trip_id is None:
            return []
        source_offers = list(
            await self._session.scalars(
                select(FlightSearchResult).where(
                    col(FlightSearchResult.trip_request_id) == source_trip_id
                )
            )
        )
        return _complete_flight_results(source_offers, trip.return_date)
```

Note `_get_cross_trip_cache` is only reached when the same-trip cache (which already applies `_complete_flight_results`) is empty, so the round-trip filter test's `source == "live"` assertion holds: an incomplete-only same-trip cache is filtered to `[]` by `_get_same_trip_cache`, there's no other trip to cross-cache from, so it falls through to live search — matching current `trips_repository.search_flights` behavior exactly (both call sites of `_complete_flight_results` filter, never fabricate).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/flight_search.py backend/tests/test_flight_search_service.py
git commit -m "feat: cross-trip cache reuse and round-trip completeness filtering in FlightSearchService"
```

---

## Task 5: Wire the route to FlightSearchService

**Files:**
- Modify: `backend/app/routes/trips.py:113-136`
- Modify: `backend/app/repositories/trips_repository.py` (delete moved functions)
- Test: existing `backend/tests/test_execution_panel_route.py`, `backend/tests/test_trip_routes.py` (regression, must keep passing unmodified)

- [ ] **Step 1: Confirm the regression tests that must keep passing without edits**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_execution_panel_route.py tests/test_trip_routes.py -v`
Expected: all currently PASS (baseline before refactor).

- [ ] **Step 2: Update the route**

In `backend/app/routes/trips.py`, replace the body of `search_trip_flights`:

```python
from app.services.flight_search import FlightSearchService, flight_provider_name

...

async def search_trip_flights(
    trip_id: int, session: AsyncSession = Depends(get_session)
) -> FlightSearchOut:
    provider = get_flight_provider(get_settings())
    await repository.get_trip(session, trip_id)
    async with execution_context(
        session, trip_id, run_model=flight_provider_name(provider)
    ) as agent_run:
        assert agent_run is not None
        outcome = await FlightSearchService(session, provider).search(trip_id, agent_run=agent_run)
    return FlightSearchOut(
        offers=[_to_flight_offer_out(offer) for offer in outcome.offers],
        unavailable_reason=outcome.unavailable_reason,
    )
```

`flight_provider_name` now has exactly one definition, in `services/flight_search.py` (added in Task 2). There is no import cycle: `services/flight_search.py` already imports from `repositories/trips_repository.py` (`ErrorCode`, `TripError`), and `routes/trips.py` already imports from both `repository` and, as of this task, `services.flight_search` directly — nothing in `trips_repository.py` needs to import `services/flight_search.py` back. Delete `flight_provider_name` from `trips_repository.py` entirely in the next step; do not leave a second copy.

- [ ] **Step 3: Delete the now-dead code from `trips_repository.py`**

Remove `search_flights`, `get_recent_flight_results`, `_record_search_flights_run`, `_to_flight_result`, `offer_summary`, and `flight_provider_name` from `backend/app/repositories/trips_repository.py` (all moved into the service, none left behind). Keep `_cheapest_first` and `_complete_flight_results` in `trips_repository.py` too — `get_trip_snapshot` still calls `_cheapest_first` directly and is out of scope for this refactor. Grep for any other caller of `repository.flight_provider_name`/`repository.offer_summary` before deleting (`grep -rn "repository\.flight_provider_name\|repository\.offer_summary\|from app.repositories.trips_repository import" backend/app`) and update it to import from `app.services.flight_search` instead. Remove now-unused imports (`AgentRun`, `AgentRunStep`, `AgentStepKind`, `ExecutionEventKind`, `record_event`, `FLIGHT_CACHE_TTL_MINUTES`, `FlightResultSource` — check each is actually unused post-deletion before removing; `FlightSearchResult` is still used by `get_trip_snapshot`).

- [ ] **Step 4: Run the full regression suite for this area**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_execution_panel_route.py tests/test_trip_routes.py tests/test_flight_provider_strategy.py -v`
Expected: all PASS, unchanged from Step 1's baseline (same test names, same assertions — no test file edits in this task).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/trips.py backend/app/repositories/trips_repository.py
git commit -m "refactor: route /flights/search through FlightSearchService"
```

---

## Task 6: Wire the planner tool to FlightSearchService

**Files:**
- Modify: `backend/app/agent/planner.py:66-124`
- Test: existing `backend/tests/test_planner_search_flights.py` (regression, must keep passing unmodified — it asserts the tool's public dict shape and trust-boundary behavior, both preserved by the service's `persist=False, allow_cross_trip_cache=False` mode)
- Test: `backend/tests/test_flight_search_service.py` (add one equivalence test)

- [ ] **Step 1: Write the equivalence regression test**

This is a regression/invariant test, not a red-green TDD step: `FlightSearchService.search` already supports both `persist`/`allow_cross_trip_cache` modes as of Task 4, so this test passes immediately. Its job is to pin the invariant explicitly before the planner tool starts depending on it in Step 3, so a future change to either mode's filtering/ordering logic gets caught here.

```python
def test_route_and_planner_tool_modes_agree_on_offer_ordering_and_shape() -> None:
    """Both callers must go through the same cheapest-first/round-trip-filter logic — this
    pins that persist=False/allow_cross_trip_cache=False changes only side effects, not the
    returned offers' content or order."""

    async def _work(session):
        trip_id = await seed_trip(session)
        offers = [
            NormalizedFlightOffer(
                carrier="B", price_usd=500.0, currency="USD", depart_at="2026-08-01T09:00:00",
                arrive_at="2026-08-01T21:00:00", stops=0, booking_token="b", raw_offer={},
            ),
            NormalizedFlightOffer(
                carrier="A", price_usd=200.0, currency="USD", depart_at="2026-08-01T10:00:00",
                arrive_at="2026-08-01T22:00:00", stops=1, booking_token="a", raw_offer={},
            ),
        ]
        route_outcome = await FlightSearchService(session, FlightSearchSpy(offers=offers)).search(
            trip_id
        )
        other_trip_id = await seed_trip(session)
        tool_outcome = await FlightSearchService(
            session, FlightSearchSpy(offers=offers)
        ).search(other_trip_id, persist=False, allow_cross_trip_cache=False)
        return route_outcome, tool_outcome

    route_outcome, tool_outcome = run_db(_work)
    assert [offer.carrier for offer in route_outcome.offers] == ["A", "B"]
    assert [offer.carrier for offer in tool_outcome.offers] == ["A", "B"]
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_flight_search_service.py -v`
Expected: PASS on first run (10 tests) — confirms the invariant already holds before Step 3 wires the planner tool to rely on it.

- [ ] **Step 3: Update the planner tool**

Replace `backend/app/agent/planner.py`'s `search_flights` body (keep the IATA shape-validation `ModelRetry` at the top unchanged):

```python
async def search_flights(
    ctx: RunContext[PlannerDeps],
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: str | None = None,
) -> dict:
    """Search real Google Flights offers between two IATA airport codes."""
    if not _IATA_CODE_PATTERN.match(departure_id) or not _IATA_CODE_PATTERN.match(arrival_id):
        raise ModelRetry(
            f"departure_id and arrival_id must be 3-letter IATA codes (e.g. JFK), got "
            f"departure_id={departure_id!r} arrival_id={arrival_id!r}"
        )

    # Route/dates never change mid-plan, and this tool must never write offers or reach across
    # trips (see FlightSearchService.search's persist/allow_cross_trip_cache — the route path,
    # not this tool, owns full persistence and cross-trip reuse).
    session, trip_id = current_trip("search_flights")
    trip = await session.get(TripRequest, trip_id)
    if trip is None:
        raise ModelRetry(f"trip {trip_id} is gone; cannot search flights for it")
    outcome = await FlightSearchService(session, ctx.deps.flight_provider).search(
        trip_id, persist=False, allow_cross_trip_cache=False
    )
    return {
        "offers": [offer_summary(offer) for offer in outcome.offers],
        "unavailable_reason": outcome.unavailable_reason,
        "source": "cached" if outcome.source == "same_trip_cache" else "live",
    }
```

Update imports: replace `from app.repositories.trips_repository import (flight_provider_name, get_recent_flight_results, offer_summary)` with `from app.services.flight_search import FlightSearchService, offer_summary`, and drop the now-unused `time` import if nothing else in the file uses it (check `web_search` — it doesn't; but `time` may still be used there, verify before removing).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_planner_search_flights.py tests/test_flight_search_service.py tests/test_planner_guardrails.py tests/test_observability.py -v`
Expected: all PASS — `test_planner_search_flights.py`'s two tests (cache reuse, trust boundary) pass unmodified because the service's same-trip-cache-first behavior with `persist=False` reproduces exactly what the old inline code did.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/planner.py backend/tests/test_flight_search_service.py
git commit -m "refactor: route planner search_flights tool through FlightSearchService"
```

---

## Task 7: ExecutionService — start_run + record_event

**Files:**
- Modify: `backend/app/agent/execution_log.py`
- Test: `backend/tests/test_execution_service.py`

**Interfaces:**
- Produces: `ExecutionService(session: AsyncSession)` with `async def start_run(self, trip_id: int, *, model: str | None = None) -> AsyncIterator[ExecutionRun]` (async context manager), `ExecutionRun` exposing `agent_run: AgentRun | None` and `async def record_event(self, kind, name, status, detail, duration_ms=None, data=None, provider=None) -> None`.
- Consumes: existing `execution_context()`, `current_trip()`, module-level `record_event()` — unchanged, kept as compatibility shims per the spec.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_execution_service.py
"""Boundary tests for ExecutionService: a thin object wrapping execution_context()/
persist_agent_run() so callers stop reaching into the ContextVar-based module functions
directly. current_trip()/record_event() stay as working compatibility shims — planner tools
still call them and must keep working unchanged."""

from app.agent.execution_log import ExecutionService, current_trip
from app.models import ExecutionEventKind
from tests.db_helpers import get_trip, run_db, seed_trip


def test_start_run_creates_an_agent_run_bound_to_the_trip() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            agent_run_id = run.agent_run.id
        return trip_id, agent_run_id

    trip_id, agent_run_id = run_db(_work)
    assert agent_run_id is not None


def test_record_event_persists_through_the_bound_contextvar() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            await run.record_event(ExecutionEventKind.API_CALL, "probe", "ok", "detail")
            # current_trip() must resolve to the same session/trip the run is bound to.
            bound_session, bound_trip_id = current_trip("test")
        return trip_id, bound_trip_id

    trip_id, bound_trip_id = run_db(_work)
    assert bound_trip_id == trip_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_execution_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'ExecutionService'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/agent/execution_log.py` (leave every existing function/class untouched):

```python
@dataclass
class ExecutionRun:
    """The narrow surface callers need once bound inside an ExecutionService run — everything
    else (ContextVar plumbing, event-sequence allocation, per-run locking) stays hidden in
    execution_context()."""

    agent_run: AgentRun | None

    async def record_event(
        self, kind: ExecutionEventKind, name: str, status: str, detail: str,
        duration_ms: int | None = None, data: dict[str, Any] | None = None,
        provider: str | None = None,
    ) -> None:
        await record_event(kind, name, status, detail, duration_ms, data, provider)


class ExecutionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @asynccontextmanager
    async def start_run(
        self, trip_id: int, *, model: str | None = None
    ) -> AsyncIterator[ExecutionRun]:
        async with execution_context(self._session, trip_id, run_model=model) as agent_run:
            yield ExecutionRun(agent_run=agent_run)
```

Add `from dataclasses import dataclass` and `from typing import Any` to the top-of-file imports if not already present (check first — `_ExecutionContext` already uses `@dataclass`/`field`, so `dataclass` is likely already imported; `Any` needs checking against `record_event`'s existing signature which already takes `data: dict[str, Any] | None`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_execution_service.py tests/test_execution_log.py -v`
Expected: all PASS — `test_execution_log.py`'s existing seq-resumption and data-payload tests pass unmodified since `execution_context`/`record_event` are untouched.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/execution_log.py backend/tests/test_execution_service.py
git commit -m "feat: add ExecutionService wrapping execution_context as start_run/ExecutionRun"
```

---

## Task 8: ExecutionService — persist_result (finalization) + concurrent event ordering

**Files:**
- Modify: `backend/app/agent/execution_log.py`
- Test: `backend/tests/test_execution_service.py`

**Interfaces:**
- Produces: `ExecutionRun.persist_result(self, *, message_history, usage, status="completed") -> AgentRun` — thin wrapper delegating to `observability.persist_agent_run`.

- [ ] **Step 1: Write the failing tests**

```python
import asyncio

from pydantic_ai.usage import RunUsage


def test_concurrent_record_event_calls_get_sequential_non_colliding_seq() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            await asyncio.gather(
                run.record_event(ExecutionEventKind.API_CALL, "a", "ok", "1"),
                run.record_event(ExecutionEventKind.API_CALL, "b", "ok", "2"),
                run.record_event(ExecutionEventKind.API_CALL, "c", "ok", "3"),
            )
        from sqlmodel import col, select

        from app.models import ExecutionEvent

        rows = list(
            await session.scalars(
                select(ExecutionEvent)
                .where(col(ExecutionEvent.trip_request_id) == trip_id)
                .order_by(col(ExecutionEvent.seq))
            )
        )
        return [event.seq for event in rows]

    seqs = run_db(_work)
    assert seqs == [1, 2, 3], f"concurrent record_event calls must not collide on seq; got {seqs}"


def test_persist_result_finalizes_the_run_and_derives_steps_from_message_history() -> None:
    async def _work(session):
        trip_id = await seed_trip(session)
        service = ExecutionService(session)
        async with service.start_run(trip_id, model="test-model") as run:
            finalized = await run.persist_result(
                message_history=[], usage=RunUsage(input_tokens=10, output_tokens=5)
            )
        return finalized

    finalized = run_db(_work)
    assert finalized.status == "completed"
    assert finalized.total_input_tokens == 10
    assert finalized.total_output_tokens == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_execution_service.py -v`
Expected: FAIL — `test_concurrent_...` should already PASS (locking already exists in `execution_context`/`record_event`; this pins the guarantee explicitly). `test_persist_result_...` FAILs with `AttributeError: 'ExecutionRun' object has no attribute 'persist_result'`.

- [ ] **Step 3: Write minimal implementation**

Add the import and method:

```python
from app.agent.observability import persist_agent_run
```

```python
    async def persist_result(
        self, *, message_history: list[Any], usage: RunUsage, status: str = "completed"
    ) -> AgentRun:
        context = _bound_context("ExecutionRun.persist_result")
        return await persist_agent_run(
            context.session, trip_request_id=context.trip_request_id, model=context.agent_run.model
            if context.agent_run is not None else "unknown",
            message_history=message_history, usage=usage, status=status,
            agent_run=context.agent_run,
        )
```

Add `RunUsage` import: `from pydantic_ai.usage import RunUsage`. Watch for an import cycle: `observability.py` — check it doesn't import `execution_log.py` back (per the report, `observability.py`'s reads were `_duration_ms`/`_find_tool_return`/`_summarize_value`/`derive_steps`/`persist_agent_run`, no import of `execution_log` noted — confirm with `grep -n "^from\|^import" backend/app/agent/observability.py` before adding the import; if a cycle exists, import `persist_agent_run` lazily inside the method instead of at module top).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_execution_service.py tests/test_observability.py tests/test_execution_log.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/execution_log.py backend/tests/test_execution_service.py
git commit -m "feat: ExecutionRun.persist_result wrapping persist_agent_run"
```

---

## Task 9: Migrate dbos_runtime.py to ExecutionService (proves the new API works end-to-end)

**Files:**
- Modify: `backend/app/dbos_runtime.py`
- Test: existing `backend/tests/test_dbos_runtime.py` (regression, must keep passing unmodified)

- [ ] **Step 1: Confirm baseline**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_dbos_runtime.py -v`
Expected: PASS (baseline before refactor).

- [ ] **Step 2: Update `_run_planner_workflow`**

In `backend/app/dbos_runtime.py`, replace the `execution_context(session, trip_id, run_model=CEREBRAS_MODEL) as observed_run` block's opening with:

```python
from app.agent.execution_log import ExecutionService

...

async with ExecutionService(session).start_run(trip_id, model=CEREBRAS_MODEL) as run:
    observed_run = run.agent_run
    ...  # existing body unchanged: builds PlannerDeps, drives agent.iter(...)
```

Convert both the success and failure paths to go through `ExecutionRun.persist_result` — do not leave `_persist_failed_run` calling `observability.persist_agent_run` directly, since that would leave two ways to finalize a run and defeat the point of the wrapper:

- Success path: replace `persist_agent_run(session, ..., agent_run=observed_run)` with `await run.persist_result(message_history=result.all_messages(), usage=result.usage)` (the default `status="completed"` matches the old call).
- Failure path (`_persist_failed_run`, called from the `UsageLimitExceeded`/`UnexpectedModelBehavior` branches): change its signature to take the bound `run: ExecutionRun` instead of `session`/`agent_run` separately, and replace its body's `persist_agent_run(..., status="failed", agent_run=observed_run)` call with `await run.persist_result(message_history=agent_run.ctx.state.message_history, usage=agent_run.ctx.state.usage, status="failed")`.

Update `_persist_failed_run`'s call sites in `_run_planner_workflow` to pass `run` instead of `session, trip_id, agent_run, observed_run`. After this, `observability.persist_agent_run` has exactly one caller left: `ExecutionRun.persist_result` — confirm with `grep -rn "persist_agent_run" backend/app` that no other call site was missed.

- [ ] **Step 3: Run the regression test**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest tests/test_dbos_runtime.py tests/test_execution_panel_route.py -v`
Expected: all PASS unchanged (same test names/assertions, no test file edits this task).

- [ ] **Step 4: Commit**

```bash
git add backend/app/dbos_runtime.py
git commit -m "refactor: migrate run_planner workflow to ExecutionService"
```

---

## Task 10: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" && uv run pytest -q`
Expected: 0 failures. If any fail, diagnose root cause per the codebase's actual error output — do not weaken assertions.

- [ ] **Step 2: Run pyrefly**

Run: `cd backend && uv run pyrefly check`
Expected: 0 errors introduced by this refactor (pre-existing errors, if any, are out of scope — note them separately, don't fix unrelated issues).

- [ ] **Step 3: Confirm no unrelated files were touched**

Run: `git diff --stat main` (or against the commit before Task 1) and confirm only files listed in "File Structure" above changed, plus whatever was already modified in the working tree before this plan started (booking/HITL, frontend, connectors, slack — untouched by this plan's tasks).

- [ ] **Step 4: Report**

Summarize: files changed, tests added/moved, confirmation booking/HITL/frontend/OpenAPI/DBOS-signatures/provider-adapters were not touched, test + pyrefly results, and any remaining architectural debt.
