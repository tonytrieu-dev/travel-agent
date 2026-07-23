"""Context-local ExecutionEvent recorder — deep call-stack code records without threading
trip_request_id through every signature. Commits immediately so a later failure still leaves
prior events recorded.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.models import ExecutionEvent, ExecutionEventKind


@dataclass
class _ExecutionContext:
    session: AsyncSession
    trip_request_id: int
    next_seq: int
    # pydantic_ai runs tool calls from the same model turn concurrently (see
    # search_flights/web_search running together); AsyncSession isn't safe for concurrent
    # use, so every record_event() write must go through this lock, one at a time.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_current: ContextVar[_ExecutionContext | None] = ContextVar("execution_context", default=None)


@asynccontextmanager
async def execution_context(session: AsyncSession, trip_request_id: int) -> AsyncIterator[None]:
    """Bind session/trip_request_id for one run. seq resumes from the trip's max so re-planned
    runs share one continuous timeline."""
    result = await session.execute(
        select(func.max(ExecutionEvent.seq)).where(
            col(ExecutionEvent.trip_request_id) == trip_request_id
        )
    )
    starting_seq = (result.scalar_one_or_none() or 0) + 1

    token = _current.set(
        _ExecutionContext(session=session, trip_request_id=trip_request_id, next_seq=starting_seq)
    )
    try:
        yield
    finally:
        _current.reset(token)


def _bound_context(caller_name: str) -> _ExecutionContext:
    context = _current.get()
    if context is None:
        raise RuntimeError(
            f"{caller_name}() called with no execution_context bound — every "
            "adapter/tool/protocol call must run inside execution_log.execution_context()"
        )
    return context


def current_trip(caller_name: str) -> tuple[AsyncSession, int]:
    """Exposed so a tool can query the trip's own data without threading session/trip_id
    through PlannerDeps."""
    context = _bound_context(caller_name)
    return context.session, context.trip_request_id


async def record_event(
    kind: ExecutionEventKind,
    name: str,
    status: str,
    detail: str,
    duration_ms: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    context = _bound_context("record_event")
    async with context.lock:
        context.session.add(
            ExecutionEvent(
                trip_request_id=context.trip_request_id,
                seq=context.next_seq,
                kind=kind,
                name=name,
                status=status,
                detail=detail,
                duration_ms=duration_ms,
                data=data,
            )
        )
        context.next_seq += 1
        await context.session.commit()
