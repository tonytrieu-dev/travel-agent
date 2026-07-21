"""The only path to register a tool on the planner agent. classification has no default, so
an unclassified tool is a TypeError at import time. BOUNDARY_CROSSING tools call
require_approval(ctx, name) themselves; the /plan flow has no approver channel, so
ctx.tool_call_approved is always false and the call is denied, never executed.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic_ai import Agent, ApprovalRequired, RunContext


class ToolClassification(StrEnum):
    READ_ONLY = "read_only"
    BOUNDARY_CROSSING = "boundary_crossing"


_registry: dict[str, ToolClassification] = {}


def register_tool(
    agent: Agent[Any, Any], func: Callable, *, classification: ToolClassification
) -> None:
    _registry[func.__name__] = classification
    agent.tool(func)


def classifications() -> dict[str, ToolClassification]:
    return dict(_registry)


def require_approval(ctx: RunContext, tool_name: str) -> None:
    if not ctx.tool_call_approved:
        raise ApprovalRequired(
            metadata={"tool": tool_name, "reason": "no approver channel available"}
        )
