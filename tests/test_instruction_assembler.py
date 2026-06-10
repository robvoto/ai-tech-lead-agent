from __future__ import annotations

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.instruction_assembler import build_agent_instruction, select_skills

from helpers import valid_settings_dict


def test_selects_multiple_relevant_skills_when_task_crosses_boundaries() -> None:
    skills = select_skills("Update AGENTS.md and backlog task JH-001 tests")

    assert [skill.name for skill in skills] == [
        "code-change",
        "backlog-management",
        "instruction-maintenance",
    ]


def test_assembled_instruction_includes_project_rules_and_selected_skill() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Fix Telegram graph runtime test",
        brief="Small runtime fix only.",
        needs_approval=False,
        approval_reason="Low risk.",
        approved=False,
        settings=settings,
    )

    assert "# Coding-Agent Handoff" in instruction
    assert "## Orchestrator identity" in instruction
    assert "AI Technical Lead Orchestrator" in instruction
    assert "## Project rules" in instruction
    assert "## Non-negotiables" in instruction
    assert "## Selected skills" in instruction
    assert "## code-change" in instruction
    assert "backlog-management" not in instruction
    assert "instruction-maintenance" not in instruction
    assert "## Stop conditions" in instruction
    assert "src/ai_tech_lead" in instruction


def test_assembled_instruction_does_not_dump_irrelevant_skills() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Update backlog item JH-001",
        brief="Backlog-only change.",
        needs_approval=False,
        approval_reason="Low risk.",
        approved=False,
        settings=settings,
    )

    assert "## backlog-management" in instruction
    assert "## instruction-maintenance" not in instruction


def test_assembled_instruction_includes_backlog_ownership_rules_from_prompt_file() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Update backlog item JH-001",
        brief="Backlog-only change.",
        needs_approval=False,
        approval_reason="Low risk.",
        approved=False,
        settings=settings,
    )

    assert "Worker coding agents must not mark backlog items complete" in instruction
    assert "Final backlog completion is owned by the orchestrator after human acceptance." in instruction
