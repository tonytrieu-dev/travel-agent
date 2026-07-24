"""Phase 5's last piece: the agent run executes durably through DBOS, not as a bare coroutine.
A ``TestModel`` swap keeps this fast/deterministic — real-model behavior is already covered by
manual verification; this guards the DBOS wiring itself (a plain call would raise before
``DBOS.launch()``, per the library's own contract)."""

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import col

from app.agent.planner import agent as planner_agent
from app.dbos_runtime import run_planner_durable
from app.models import AgentRun, AgentRunStep, ExecutionEvent, ExecutionEventKind
from app.schemas import ClarificationOut
from tests.db_helpers import TEST_DATABASE_URL, seed_trip


async def _seed_trip_id() -> int:
    # Not run_db(): that helper wraps its work in asyncio.run(), which cannot be called from
    # inside a pytest-asyncio test's already-running event loop.
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            trip_id = await seed_trip(session)
            await session.commit()
            return trip_id
    finally:
        await engine.dispose()


async def test_run_planner_durable_persists_a_real_agent_run_for_the_panel(
    client, monkeypatch
) -> None:
    """Guards the Phase 6.4 wiring: a durable planner run must leave a real AgentRun row behind
    (tokens + model from the actual run), not just return output with no observability trail."""
    trip_id = await _seed_trip_id()

    # Empty itinerary: this test guards DBOS wiring, not grounding, so give the output validator
    # nothing to ground (a dummy itinerary with activities would trip reject_ungrounded_itinerary).
    class TavilyActivityProvider:
        def __init__(self, api_key: str) -> None:
            pass

        async def search(self, query: str, max_results: int = 5) -> list:
            return []

    monkeypatch.setattr("app.dbos_runtime.TavilyActivityProvider", TavilyActivityProvider)
    with planner_agent.override(
        model=TestModel(call_tools=["web_search"], custom_output_args={"days": []})
    ):
        await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            agent_runs = list(
                await session.scalars(
                    select(AgentRun).where(col(AgentRun.trip_request_id) == trip_id)
                )
            )
            events = list(
                await session.scalars(
                    select(ExecutionEvent).where(
                        col(ExecutionEvent.trip_request_id) == trip_id
                    )
                )
            )
    finally:
        await engine.dispose()

    assert len(agent_runs) == 1, (
        "run_planner_durable must persist an AgentRun row so /execution has real data to serve, "
        f"without duplicating the context-created row; got {len(agent_runs)}"
    )
    agent_run = agent_runs[0]
    assert agent_run.total_input_tokens > 0, (
        f"AgentRun must carry the run's real token usage, got "
        f"total_input_tokens={agent_run.total_input_tokens}"
    )
    assert events
    assert all(event.agent_run_id == agent_run.id for event in events)
    assert any(
        event.kind is ExecutionEventKind.PROTOCOL and event.name == "Pydantic AI"
        for event in events
    )
    assert any(
        event.name == "web_search" and event.provider == "Tavily" for event in events
    )


async def test_run_planner_durable_persists_a_failed_run_with_its_steps_so_far(client) -> None:
    """A crash partway through a run (e.g. a provider 413 after some exchanges already happened)
    must not erase those exchanges — the execution panel needs the real partial history, not
    nothing, or a reviewer can never see what the agent actually did before it failed.
    """
    trip_id = await _seed_trip_id()
    call_count = 0

    def _crash_on_second_call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # allow_text_output is False for this structured-output agent, so a bare TextPart
            # forces one genuine retry round-trip — a real step before the crash, with no need
            # for a real tool call.
            return ModelResponse(parts=[TextPart(content="thinking")])
        raise RuntimeError("simulated provider 413")

    with planner_agent.override(model=FunctionModel(_crash_on_second_call)):
        with pytest.raises(RuntimeError, match="simulated provider 413"):
            await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            agent_runs = list(
                await session.scalars(
                    select(AgentRun).where(col(AgentRun.trip_request_id) == trip_id)
                )
            )
            agent_run = agent_runs[0] if agent_runs else None
            steps = (
                list(
                    await session.scalars(
                        select(AgentRunStep).where(
                            col(AgentRunStep.agent_run_id) == agent_run.id
                        )
                    )
                )
                if agent_run is not None
                else []
            )
            events = list(
                await session.scalars(
                    select(ExecutionEvent).where(
                        col(ExecutionEvent.trip_request_id) == trip_id
                    )
                )
            )
    finally:
        await engine.dispose()

    assert agent_run is not None, (
        "a run that crashes mid-way must still persist an AgentRun row, not vanish as if the "
        "agent never ran at all"
    )
    assert agent_run.status == "failed"
    assert len(agent_runs) == 1
    assert events and all(event.agent_run_id == agent_run.id for event in events)
    assert len(steps) == 1, (
        f"the one exchange that completed before the crash must survive as a real step, got "
        f"{len(steps)} steps"
    )


async def test_run_planner_durable_asks_for_clarification_instead_of_crashing_when_output_retries_are_exhausted(
    client,
) -> None:
    """A model that never produces valid structured output (e.g. no groundable activities exist)
    exhausts pydantic-ai's output-retry budget and raises UnexpectedModelBehavior internally —
    the workflow must turn that into a ClarificationOut, not propagate a raw crash to /plan.
    """
    trip_id = await _seed_trip_id()

    def _never_produce_valid_output(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # allow_text_output is False for this structured-output agent, so a bare TextPart always
        # forces an output retry — repeating it exhausts the budget deterministically.
        return ModelResponse(parts=[TextPart(content="thinking")])

    with planner_agent.override(model=FunctionModel(_never_produce_valid_output)):
        output = await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    assert isinstance(output, ClarificationOut), (
        f"an exhausted output-retry budget must degrade to ClarificationOut instead of raising "
        f"or returning {type(output)}"
    )

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            agent_run = await session.scalar(
                select(AgentRun).where(col(AgentRun.trip_request_id) == trip_id)
            )
    finally:
        await engine.dispose()

    assert agent_run is not None and agent_run.status == "failed", (
        "the exhausted-retries run must still leave a real failed AgentRun for the panel, not "
        "vanish just because the caller got a clarification instead of an exception"
    )


async def test_run_planner_durable_asks_for_clarification_instead_of_crashing_when_usage_limit_is_exceeded(
    client, monkeypatch
) -> None:
    """gpt-oss-120b on Cerebras is rate-limited to 30K tokens/minute, and pydantic-ai resends the
    growing message history on every step — a real, expected outcome on a research-heavy trip,
    not a bug. UsageLimitExceeded (covers the token, tool-call, and request ceilings alike) must
    degrade to a distinct ClarificationOut, not propagate a raw crash to /plan.
    """
    trip_id = await _seed_trip_id()
    monkeypatch.setattr(
        "app.dbos_runtime.default_usage_limits", lambda: UsageLimits(request_limit=1)
    )

    def _never_finishes_within_the_request_limit(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        # allow_text_output is False, so a bare TextPart always forces another request — with
        # request_limit=1 that next request deterministically trips UsageLimitExceeded.
        return ModelResponse(parts=[TextPart(content="thinking")])

    with planner_agent.override(model=FunctionModel(_never_finishes_within_the_request_limit)):
        output = await run_planner_durable(trip_id, "Plan me a trip to Paris.")

    assert isinstance(output, ClarificationOut), (
        f"an exceeded usage limit must degrade to ClarificationOut instead of raising or "
        f"returning {type(output)}"
    )
    assert output.questions != [
        "I couldn't find enough verified activity information to complete this "
        "itinerary. Could you narrow the destination or share specific interests "
        "to search for?"
    ], "a usage-limit clarification must not reuse the exhausted-output-retries message — the two failures have different causes and different asks"

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            agent_run = await session.scalar(
                select(AgentRun).where(col(AgentRun.trip_request_id) == trip_id)
            )
    finally:
        await engine.dispose()

    assert agent_run is not None and agent_run.status == "failed", (
        "the usage-limit-exceeded run must still leave a real failed AgentRun for the panel, not "
        "vanish just because the caller got a clarification instead of an exception"
    )
