"""Planner tools stay compatible with the configured model protocol."""

from pydantic_ai.models import Model, ModelRequestParameters


def test_cerebras_request_tools_have_uniform_strict_mode() -> None:
    """Cerebras rejects a mixed strict request. Pydantic AI infers the final-output tools as
    strict-compatible, so planner function tools must opt into strict mode too."""
    from app.agent.planner import agent as planner_agent

    output_toolset = planner_agent._output_toolset
    assert output_toolset is not None
    model = planner_agent.model
    assert isinstance(model, Model)

    _, request_parameters = model.prepare_request(
        None,
        ModelRequestParameters(
            function_tools=[
                tool.tool_def for tool in planner_agent._function_toolset.tools.values()
            ],
            output_tools=output_toolset._tool_defs,
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
