"""Test harness: a real Postgres (``travel_agent_test``, migrated to head so the append-only
triggers exist), the FastAPI app wired to it via a synchronous Starlette ``TestClient``, and
per-test truncation.

Why sync ``TestClient`` rather than an async httpx client: pytest-bdd 8 runs step functions
synchronously, so the request calls inside steps must be synchronous too. ``TestClient`` drives
the async app through an anyio portal on a single background event loop — which is exactly what
makes ``test_double_execute_books_once`` real: two threaded ``execute`` calls become two
concurrent tasks on that one loop, each with its own DB connection, so the ``SELECT ... FOR
UPDATE`` in one genuinely blocks the other in Postgres.

Direct DB seeding (a booking already CONFIRMED, or one whose fare has expired) runs through
``run_db`` on its own short-lived engine/loop, fully committed and disposed before the request —
so app connections (portal loop) and seed connections (seed loop) never cross event loops.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db_helpers import TEST_DATABASE_URL, run_db

_ALL_TABLES = (
    "booking_transition, execution_event, agent_run_step, agent_run, hitl_booking_log, "
    "itinerary, flight_search_result, trip_request, user_account"
)


@pytest.fixture(autouse=True)
def _truncate_between_tests() -> None:
    async def _truncate(session: AsyncSession) -> None:
        await session.execute(text(f"TRUNCATE {_ALL_TABLES} RESTART IDENTITY CASCADE"))

    run_db(_truncate)


@dataclass
class BookingOptionsFetchSpy:
    """Stands in for the real SearchApi booking-options call and counts invocations."""

    options: list[dict] = field(
        default_factory=lambda: [
            {"provider": "Test Air", "price_usd": 512.0, "booking_url": "https://example.test/book"}
        ]
    )
    calls: int = 0
    should_fail: bool = False

    async def __call__(self, flight_search_result) -> list[dict]:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("simulated upstream failure")
        return self.options


@pytest.fixture
def booking_options_spy() -> BookingOptionsFetchSpy:
    return BookingOptionsFetchSpy()


@pytest.fixture
def client(booking_options_spy: BookingOptionsFetchSpy):
    from starlette.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from app.routes.booking import get_booking_options_fetcher

    app_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    app_session_factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async def _override_get_session():
        async with app_session_factory() as session:
            yield session

    def _override_fetcher() -> Callable:
        return booking_options_spy

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_booking_options_fetcher] = _override_fetcher
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    asyncio.run(app_engine.dispose())
