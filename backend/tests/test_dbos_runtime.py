"""Phase 5's last piece: the agent run executes durably through DBOS, not as a bare coroutine.
A ``TestModel`` swap keeps this fast/deterministic — real-Gemini behavior is already covered by
manual verification; this guards the DBOS wiring itself (a plain call would raise before
``DBOS.launch()``, per the library's own contract)."""

from pydantic_ai.models.test import TestModel

from app.agent.planner import agent as planner_agent
from app.dbos_runtime import run_planner_durable
from app.schemas import ClarificationOut, ItineraryOut


async def test_run_planner_durable_executes_the_agent_through_a_dbos_workflow(client) -> None:
    with planner_agent.override(model=TestModel(call_tools=[])):
        output = await run_planner_durable("Plan me a trip to Paris.")

    assert isinstance(output, ItineraryOut | ClarificationOut), (
        f"run_planner_durable must return the agent's real structured output, got {type(output)}"
    )
