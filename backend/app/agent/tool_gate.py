"""The only path to register a tool on the planner agent. classification has no default, so
an unclassified tool is a TypeError at import time.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic_ai import Agent


class ToolClassification(StrEnum):
    READ_ONLY = "read_only"


_registry: dict[str, ToolClassification] = {}


def register_tool(
    agent: Agent[Any, Any], func: Callable, *, classification: ToolClassification
) -> None:
    _registry[func.__name__] = classification
    agent.tool(strict=True)(func)


def classifications() -> dict[str, ToolClassification]:
    return dict(_registry)
