from __future__ import annotations

from pathlib import Path

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.instruction_assembler import build_agent_instruction, select_skills


def _write_target_project_pack(project_root: Path) -> None:
    (project_root / ".skills" / "code-change").mkdir(parents=True)
    (project_root / ".skills" / "backlog-management").mkdir(parents=True)
    (project_root / ".skills" / "instruction-maintenance").mkdir(parents=True)
    (project_root / ".skills" / "INDEX.md").write_text(
        "# Skills Index\n\n"
        "- `code-change/SKILL.md` - Target repo code changes.\n"
        "- `backlog-management/SKILL.md` - Target repo backlog handling.\n"
        "- `instruction-maintenance/SKILL.md` - Target repo instruction files.\n",
        encoding="utf-8",
    )
    (project_root / ".skills" / "code-change" / "SKILL.md").write_text(
        "---\nname: code-change\n---\n\n# Target Repo Code Change\nTarget repo code rules.\n",
        encoding="utf-8",
    )
    (project_root / ".skills" / "backlog-management" / "SKILL.md").write_text(
        "---\nname: backlog-management\n---\n\n# Target Repo Backlog\nTarget repo backlog rules.\n",
        encoding="utf-8",
    )
    (project_root / ".skills" / "instruction-maintenance" / "SKILL.md").write_text(
        "---\nname: instruction-maintenance\n---\n\n"
        "# Target Repo Instructions\nTarget repo instruction rules.\n",
        encoding="utf-8",
    )
    (project_root / "AGENTS.md").write_text(
        "# AGENTS.md\n\n"
        "## Default workflow\n"
        "Use the target repo docs first.\n\n"
        "## Navigation\n"
        "- Target docs only\n\n"
        "## Universal rules\n"
        "- Target repo rule\n\n"
        "## Finish report\n"
        "- Target finish format\n",
        encoding="utf-8",
    )


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
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
    )

    assert "# Coding-Agent Handoff" in instruction
    assert "## Orchestrator identity" not in instruction
    assert "## Project context" in instruction
    assert "## AI Tech Lead core" in instruction
    assert "act as a technical lead and coding-agent runtime" in instruction
    assert "## Reusable runtime skills" in instruction
    assert "## bounded-delivery" in instruction
    assert "## result-reporting" in instruction
    assert "## research-and-approval" in instruction
    assert "## Project rules" in instruction
    assert "## Universal rules" in instruction
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
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
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
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
    )

    assert "Worker coding agents must not freely edit backlog storage" in instruction
    assert (
        "Final backlog completion is owned by the orchestrator after human acceptance."
        in instruction
    )


def test_assembled_instruction_includes_current_task_feedback_only() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Update Telegram clarification flow",
        brief="Pause and collect a task-local clarification.",
        formulated_task="",
        task_feedback=[
            "Keep the change task-local.",
            "Do not update global prompts or rules.",
        ],
        needs_approval=False,
        approved=False,
        settings=settings,
    )

    assert "## Task feedback" in instruction
    assert "Apply this only to the current task." in instruction
    assert "Keep the change task-local." in instruction
    assert "Do not update global prompts or rules." in instruction


def test_assembled_instruction_includes_evidence_sources_when_provided() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Fix Telegram graph runtime test",
        brief="Small runtime fix only.",
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
        research_evidence=[
            (
                "local-doc: LangGraph hard rules for approval gates | "
                "docs/research/langgraph-approval-hard-rules.md"
            ),
            (
                "online-doc: LangGraph interrupts | "
                "https://docs.langchain.com/oss/python/langgraph/interrupts"
            ),
        ],
    )

    assert "## Research evidence" in instruction
    assert "LangGraph hard rules for approval gates" in instruction
    assert "https://docs.langchain.com/oss/python/langgraph/interrupts" in instruction
    assert "Relevant local and official documentation consulted for this task:" in instruction


def test_assembled_instruction_omits_evidence_sources_when_not_provided() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Fix Telegram graph runtime test",
        brief="Small runtime fix only.",
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
    )

    assert "## Research evidence" not in instruction


def test_assembled_instruction_has_no_duplicate_request_in_brief() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Fix the Telegram polling loop",
        brief="Relevant files:\n- src/ai_tech_lead",
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
    )

    assert instruction.count("Fix the Telegram polling loop") == 1


def test_assembled_instruction_approval_state_has_no_approval_reason() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Small refactor",
        brief="Context here.",
        formulated_task="",
        task_feedback=[],
        needs_approval=True,
        approved=False,
        settings=settings,
    )

    approval_section = instruction.split("## Approval state")[1].split("##")[0]
    assert "Needs approval: True" in approval_section
    assert "Approved: False" in approval_section
    assert "Approval reason" not in approval_section


def test_select_skills_uses_target_project_skill_files(tmp_path: Path) -> None:
    _write_target_project_pack(tmp_path)

    skills = select_skills(
        "Update AGENTS.md and backlog task JH-001 tests",
        project_root=tmp_path,
    )

    assert [skill.name for skill in skills] == [
        "code-change",
        "backlog-management",
        "instruction-maintenance",
    ]
    assert all(skill.path.is_relative_to(tmp_path) for skill in skills)


def test_assembled_instruction_uses_target_project_rules_and_skills(tmp_path: Path) -> None:
    _write_target_project_pack(tmp_path)
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Fix Telegram graph runtime test",
        brief="Small runtime fix only.",
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
        project_root=tmp_path,
    )

    assert "Use the target repo docs first." in instruction
    assert "Target repo rule" in instruction
    assert "## code-change" in instruction
    assert "Target repo code rules." in instruction
    assert "Task changes code or runtime behaviour." in instruction
    assert "Use before modifying existing code." not in instruction
    assert "## AI Tech Lead core" in instruction
    assert "## bounded-delivery" in instruction
    assert "## result-reporting" in instruction


def test_runtime_core_research_skill_is_not_loaded_for_simple_non_research_task() -> None:
    settings = parse_settings(valid_settings_dict())

    instruction = build_agent_instruction(
        request="Rename a small helper function",
        brief="Small local rename only.",
        formulated_task="",
        task_feedback=[],
        needs_approval=False,
        approved=False,
        settings=settings,
    )

    assert "## bounded-delivery" in instruction
    assert "## result-reporting" in instruction
    assert "## research-and-approval" not in instruction


def test_select_skills_requires_target_project_skills_index(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"\.skills/INDEX\.md"):
        select_skills("Fix runtime test", project_root=tmp_path)


def test_select_skills_requires_selected_target_project_skill_file(tmp_path: Path) -> None:
    (tmp_path / ".skills").mkdir()
    (tmp_path / ".skills" / "INDEX.md").write_text("# Skills Index\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="Selected target-project skill file not found"):
        select_skills("Fix runtime test", project_root=tmp_path)
