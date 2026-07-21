"""Automatic ExecutionEvent recorder for the Agent Execution Panel.

A context-local binding (``contextvars``) holds the current run's session, trip_request_id,
and next sequence number, so deep call-stack code (adapters, tools, protocol checkpoints) can
call ``record_event`` without every function signature threading trip_request_id through. This
is what makes recording automatic rather than a per-call-site opt-in — no code path can forget.

Each event commits immediately rather than waiting for the end of the run: the append-only
trigger on ``execution_event`` (Alembic migration) means once committed it can never be edited
or deleted, so a run that later fails still leaves the events recorded up to that point.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.models import ExecutionEvent, ExecutionEventKind


@dataclass
class _ExecutionContext:
    session: AsyncSession
    trip_request_id: int
    next_seq: int


_current: ContextVar[_ExecutionContext | None] = ContextVar("execution_context", default=None)


@asynccontextmanager
async def execution_context(session: AsyncSession, trip_request_id: int) -> AsyncIterator[None]:
    """Bind ``session``/``trip_request_id`` for the duration of one agent run. ``seq`` resumes
    from this trip's highest existing value, since a trip can be re-planned across multiple
    runs and every event for a trip must sort into one continuous, gap-free timeline."""
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


async def record_event(
    kind: ExecutionEventKind,
    name: str,
    status: str,
    detail: str,
    duration_ms: int | None = None,
) -> None:
    context = _current.get()
    if context is None:
        raise RuntimeError(
            f"record_event({name!r}) called with no execution_context bound — every "
            "adapter/tool/protocol call must run inside execution_log.execution_context()"
        )
    context.session.add(
        ExecutionEvent(
            trip_request_id=context.trip_request_id,
            seq=context.next_seq,
            kind=kind,
            name=name,
            status=status,
            detail=detail,
            duration_ms=duration_ms,
        )
    )
    context.next_seq += 1
    await context.session.commit()
