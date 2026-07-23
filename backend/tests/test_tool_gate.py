"""Planner tools stay read-only and compatible with the configured model protocol."""

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.test import TestModel

from app.agent.tool_gate import ToolClassification, classifications, register_tool


def test_registering_a_tool_without_classification_is_a_startup_typeerror() -> None:
    """register_tool takes no default classification, so wiring a tool without one fails loud at
    import time instead of silently registering an unclassified (possibly write) tool — the
    fail-closed guarantee DECISIONS.md relies on. A default kwarg here would erode it silently."""
    probe_agent = Agent(TestModel())

    def unclassified_tool(context: RunContext) -> str:
        return "should never register"

    with pytest.raises(TypeError):
        register_tool(probe_agent, unclassified_tool)  # type: ignore[call-arg]


def test_every_real_planner_tool_is_classified_and_read_only() -> None:
    # Importing the planner runs register_tool for each tool — the only registration path, which
    # records the classification in this public registry. Assert that surface, not the agent's
    # private toolset internals.
    import app.agent.planner  # noqa: F401  (import triggers tool registration as a side effect)

    registry = classifications()

    assert set(registry) == {"search_flights", "web_search"}, (
        f"the planner must register exactly its two tools through register_tool (the fail-closed "
        f"path); a tool added outside it, or a renamed tool, shows up here as drift — got {registry}"
    )
    assert set(registry.values()) == {ToolClassification.READ_ONLY}, (
        f"today's tools must both be READ_ONLY - wiring a write tool into the agent must go "
        f"red immediately; got {registry}"
    )


def test_cerebras_request_tools_have_uniform_strict_mode() -> None:
    """Cerebras rejects a mixed strict request. Pydantic AI infers the final-output tools as
    strict-compatible, so planner function tools must opt into strict mode too."""
    from app.agent.planner import agent as planner_agent

    output_toolset = planner_agent._output_toolset  # noqa: SLF001
    assert output_toolset is not None
    model = planner_agent.model
    assert isinstance(model, Model)

    _, request_parameters = model.prepare_request(
        None,
        ModelRequestParameters(
            function_tools=[
                tool.tool_def for tool in planner_agent._function_toolset.tools.values()  # noqa: SLF001
            ],
            output_tools=output_toolset._tool_defs,  # noqa: SLF001
            output_mode="tool",
            allow_text_output=False,
        ),
    )

    strict_values = {
        tool_definition.strict
        for tool_definition in [
            *request_parameters.function_tools,
            *request_parameters.output_tools,
        ]
    }
    assert strict_values == {True}, (
        f"all Cerebras tools must resolve to strict=True before the request is sent; "
        f"got {strict_values}"
    )
