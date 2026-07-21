"""Test 10 (plan test strategy): the tool classification/approval-gate registry is fail-closed.
Guards (a) every registered tool carries an explicit classification and today's real tools are
both READ_ONLY, (b) an unclassified registration is a startup error, (c) a BOUNDARY_CROSSING
tool with no approver channel is denied, never executed.
"""

import pytest
from pydantic_ai import Agent, RunContext, UserError
from pydantic_ai.models.test import TestModel

from app.agent.tool_gate import ToolClassification, classifications, register_tool, require_approval


def test_every_real_planner_tool_is_classified_and_read_only() -> None:
    from app.agent.planner import agent as planner_agent

    registered_tool_names = set(planner_agent._function_toolset.tools.keys())  # noqa: SLF001
    classified_tool_names = set(classifications().keys())

    assert registered_tool_names == classified_tool_names, (
        f"every tool on the planner agent must be classified and vice versa (drift guard); "
        f"agent tools={registered_tool_names}, classified tools={classified_tool_names}"
    )
    assert {classifications()[name] for name in registered_tool_names} == {
        ToolClassification.READ_ONLY
    }, (
        f"today's tools must both be READ_ONLY - wiring a write tool into the agent must go "
        f"red immediately; got {classifications()}"
    )


def test_registering_a_tool_without_classification_is_a_startup_typeerror() -> None:
    probe_agent = Agent(TestModel())

    def unclassified_tool(ctx: RunContext) -> str:
        return "should never register"

    with pytest.raises(TypeError):
        register_tool(probe_agent, unclassified_tool)  # type: ignore[call-arg]


def test_boundary_crossing_tool_with_no_approver_is_denied_not_executed() -> None:
    executed_calls: list[bool] = []
    probe_agent = Agent(TestModel(call_tools=["probe_write"]))

    def probe_write(ctx: RunContext) -> str:
        require_approval(ctx, "probe_write")
        executed_calls.append(True)
        return "should never execute"

    register_tool(probe_agent, probe_write, classification=ToolClassification.BOUNDARY_CROSSING)

    with pytest.raises(UserError):
        probe_agent.run_sync("call probe_write")

    assert executed_calls == [], (
        f"a denied boundary-crossing call must never run past the approval check, but ran "
        f"{len(executed_calls)} time(s)"
    )
