"""Test 7 (plan test strategy): the observability builder derives AgentRun/AgentRunStep from a
real Pydantic AI message history + usage() — the transformation the Agent Execution Panel
depends on. Built from real pydantic_ai message/usage types, not hand-rolled dict mocks: this
guards our own derivation logic against the real shape, not a value we configured ourselves.
"""

from datetime import UTC, datetime, timedelta

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage
from sqlalchemy import select
from sqlmodel import col

from app.agent.observability import persist_agent_run
from app.models import AgentRunStep, AgentStepKind
from tests.db_helpers import run_db, seed_trip


def _two_tool_call_history() -> list:
    t0 = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)
    return [
        ModelRequest(parts=[UserPromptPart(content="Plan my trip to Paris")], timestamp=t0),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="search_flights",
                    args={"departure_id": "JFK", "arrival_id": "CDG"},
                    tool_call_id="call_1",
                )
            ],
            usage=RequestUsage(input_tokens=120, output_tokens=15),
            model_name="gemini-3-flash",
            timestamp=t0 + timedelta(seconds=1),
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search_flights",
                    content=[{"carrier": "Air France", "price_usd": 772.0}],
                    tool_call_id="call_1",
                    timestamp=t0 + timedelta(seconds=2),
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="web_search",
                    args={"query": "gentle low-intensity activities Paris"},
                    tool_call_id="call_2",
                )
            ],
            usage=RequestUsage(input_tokens=180, output_tokens=20),
            model_name="gemini-3-flash",
            timestamp=t0 + timedelta(seconds=3),
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="web_search",
                    content=[{"title": "Seine river cruise", "url": "https://example.test/seine"}],
                    tool_call_id="call_2",
                    timestamp=t0 + timedelta(seconds=4),
                )
            ]
        ),
        ModelResponse(
            parts=[TextPart(content="Here is your day-by-day itinerary...")],
            usage=RequestUsage(input_tokens=300, output_tokens=250),
            model_name="gemini-3-flash",
            timestamp=t0 + timedelta(seconds=5),
        ),
    ]


def test_persisted_agent_run_sums_tokens_and_orders_one_step_per_tool_call() -> None:
    message_history = _two_tool_call_history()
    run_usage = RunUsage(input_tokens=600, output_tokens=285, requests=3, tool_calls=2)

    async def _work(session):
        trip_id = await seed_trip(session)
        agent_run = await persist_agent_run(
            session,
            trip_request_id=trip_id,
            model="gemini-3-flash",
            message_history=message_history,
            usage=run_usage,
        )
        steps = list(
            await session.scalars(
                select(AgentRunStep)
                .where(col(AgentRunStep.agent_run_id) == agent_run.id)
                .order_by(col(AgentRunStep.seq))
            )
        )
        return agent_run, steps

    agent_run, steps = run_db(_work)

    assert (agent_run.total_input_tokens, agent_run.total_output_tokens) == (600, 285), (
        f"AgentRun must persist the run's aggregated usage() totals, not per-response tokens; "
        f"got ({agent_run.total_input_tokens}, {agent_run.total_output_tokens})"
    )

    tool_steps = [step for step in steps if step.kind is AgentStepKind.TOOL]
    assert [step.name for step in tool_steps] == ["search_flights", "web_search"], (
        f"expected one AgentRunStep per tool call in call order, got "
        f"{[step.name for step in tool_steps]}"
    )
    assert all(step.status == "completed" for step in tool_steps), (
        "every tool call in this fixture has a matching ToolReturnPart, so none should be "
        "derived as missing a result"
    )
    assert tool_steps[0].output_summary is not None and "Air France" in tool_steps[0].output_summary, (
        f"a tool step's output_summary must carry its real ToolReturnPart content, not be "
        f"dropped; got {tool_steps[0].output_summary!r}"
    )
