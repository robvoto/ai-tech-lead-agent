from __future__ import annotations

from dataclasses import replace

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_workflow_graph import (
    NodeName,
    build_graph,
    route_after_approval,
    route_after_brief,
    run_coding_agent_node,
)

from helpers import valid_settings_dict


def graph_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "request": "Backlog item: JH-001\nTitle: Local placeholder",
        "brief": "",
        "needs_approval": False,
        "approval_reason": "Safe local work.",
        "approved": False,
        "agent_instruction": "",
        "coding_agent_result": "",
    }
    state.update(overrides)
    return state


def test_routes_follow_explicit_approval_state() -> None:
    assert route_after_brief(graph_state(needs_approval=True)) == NodeName.APPROVAL_REQUIRED
    assert route_after_brief(graph_state(needs_approval=False)) == NodeName.CREATE_AGENT_INSTRUCTION
    assert route_after_approval(graph_state(approved=True)) == NodeName.CREATE_AGENT_INSTRUCTION
    assert route_after_approval(graph_state(approved=False)) == NodeName.END_NODE


def test_run_coding_agent_node_override_can_disable_execution(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    seen_execute_values: list[bool] = []

    def fake_load_settings():
        return settings

    def fake_run_coding_agent(agent_instruction, project_root, settings):
        seen_execute_values.append(settings.execute_coding_agent)

        class Result:
            def summary(self) -> str:
                return "disabled"

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent)

    state = run_coding_agent_node(
        graph_state(agent_instruction="Do the task"),
        execute_coding_agent_override=False,
    )

    assert seen_execute_values == [False]
    assert state["coding_agent_result"] == "disabled"


def test_graph_runs_to_disabled_coding_agent_result(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    def fake_load_settings():
        return settings

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    app = build_graph(execute_coding_agent_override=False)

    result = app.invoke(graph_state())

    assert "Code runs successfully" in result["brief"]
    assert "Complete this backlog task" in result["agent_instruction"]
    assert "Coding agent execution disabled by settings." in result["coding_agent_result"]
