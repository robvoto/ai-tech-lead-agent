from __future__ import annotations

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.instruction_assembler import build_agent_instruction, select_skills


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

    assert "Worker coding agents must not freely edit the Excel workbook" in instruction
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
