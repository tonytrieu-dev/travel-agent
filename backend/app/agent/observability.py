"""Derives AgentRun/AgentRunStep rows from a real message history + usage — never fabricated;
a missing field is left null."""

import json

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, AgentRunStep, AgentStepKind, utcnow


def _duration_ms(start, end) -> int | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000)


def _find_tool_return(
    message_history: list[ModelMessage], tool_call_id: str, search_from_index: int
) -> ToolReturnPart | None:
    for message in message_history[search_from_index:]:
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_call_id == tool_call_id:
                return part
    return None


def _summarize_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def derive_steps(message_history: list[ModelMessage]) -> list[AgentRunStep]:
    """One step per model call plus one step per tool call, in the order they occurred."""
    steps: list[AgentRunStep] = []
    seq = 1
    previous_timestamp = message_history[0].timestamp if message_history else None

    for index, message in enumerate(message_history):
        if not isinstance(message, ModelResponse):
            continue

        text_parts = [part.content for part in message.parts if isinstance(part, TextPart)]
        steps.append(
            AgentRunStep(
                seq=seq,
                kind=AgentStepKind.MODEL,
                name=message.model_name or "model",
                status="completed",
                duration_ms=_duration_ms(previous_timestamp, message.timestamp),
                input_summary=None,
                output_summary=" ".join(text_parts) or None,
                tokens=message.usage.output_tokens if message.usage else None,
            )
        )
        seq += 1

        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            tool_return = _find_tool_return(message_history, part.tool_call_id, index + 1)
            steps.append(
                AgentRunStep(
                    seq=seq,
                    kind=AgentStepKind.TOOL,
                    name=part.tool_name,
                    status="completed" if tool_return is not None else "no_result",
                    duration_ms=_duration_ms(
                        message.timestamp, tool_return.timestamp if tool_return else None
                    ),
                    input_summary=_summarize_value(part.args),
                    output_summary=_summarize_value(tool_return.content) if tool_return else None,
                    tokens=None,
                )
            )
            seq += 1

        previous_timestamp = message.timestamp

    return steps


async def persist_agent_run(
    session: AsyncSession,
    *,
    trip_request_id: int,
    model: str,
    message_history: list[ModelMessage],
    usage: RunUsage,
    status: str = "completed",
    agent_run: AgentRun | None = None,
) -> AgentRun:
    """Persist the derived AgentRun + its ordered AgentRunStep rows in one transaction.

    Called on both success and failure (status="failed") — a run that 413'd partway through
    still leaves whatever steps ran before the crash on the record, not just the eventual
    success, so the execution panel shows the real run history rather than only the last win.
    """
    steps = derive_steps(message_history)
    total_ms = _duration_ms(
        message_history[0].timestamp if message_history else None,
        message_history[-1].timestamp if message_history else None,
    )

    if agent_run is None:
        agent_run = AgentRun(trip_request_id=trip_request_id, status=status, model=model)
    agent_run.status = status
    agent_run.model = model
    agent_run.total_input_tokens = usage.input_tokens
    agent_run.total_output_tokens = usage.output_tokens
    agent_run.total_ms = total_ms or 0
    agent_run.finished_at = utcnow()
    session.add(agent_run)
    await session.flush()
    assert agent_run.id is not None, "agent_run must be flushed before steps reference its id"

    for step in steps:
        step.agent_run_id = agent_run.id
        session.add(step)

    await session.commit()
    return agent_run
