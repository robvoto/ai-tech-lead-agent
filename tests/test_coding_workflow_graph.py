from __future__ import annotations

from dataclasses import replace

from langgraph.checkpoint.memory import MemorySaver

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
            message = "disabled"

            def summary(self) -> str:
                return "disabled"

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
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
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    app = build_graph(execute_coding_agent_override=False)

    result = app.invoke(graph_state())

    assert "Code runs successfully" in result["brief"]
    assert result["needs_approval"] is True
    assert "AI risk review is off, so I need your approval before continuing." in result["approval_reason"]
    assert result["approved"] is False
    assert result["agent_instruction"] == ""
    assert result["coding_agent_result"] == ""


def test_rejected_graph_resumes_without_coding_agent_result_index_error(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    def fake_load_settings():
        return settings

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-reject-path"}}

    app.invoke(graph_state(), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.APPROVAL_REQUIRED,)

    app.update_state(thread_config, {"approved": False})
    app.invoke(None, config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approved"] is False
    assert final_state.values["coding_agent_result"] == ""
