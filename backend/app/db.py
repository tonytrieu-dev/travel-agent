"""Async database engine and the FastAPI session dependency.

One engine per process; one session per request. Sessions are handed out through
``get_session`` so routes never construct their own — that keeps transaction boundaries in
one place and makes the append-only audit writes share the caller's transaction.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = create_async_engine(
    get_settings().database_url,
    echo=False,
    pool_pre_ping=True,  # drop connections the DB has already closed rather than erroring mid-request
)

_session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that is always closed after the request."""
    async with _session_factory() as session:
        yield session


def get_engine():
    """Expose the engine for lifespan startup checks and tests."""
    return _engine
