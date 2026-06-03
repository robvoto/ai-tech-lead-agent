from __future__ import annotations

from typing import Any


def valid_settings_dict() -> dict[str, Any]:
    return {
        "backlog_path": "docs/BACKLOG.md",
        "max_runtime_minutes": 20,
        "coding_agent_command": "codex",
        "coding_agent_args": ["--ask-for-approval", "never", "exec"],
        "execute_coding_agent": False,
        "allowed_directories": ["src/ai_tech_lead", "docs", "tests"],
        "watched_directories": ["src/ai_tech_lead", "docs"],
        "brief_constraints": ["Keep the change small"],
        "acceptance_criteria": [
            "Code runs successfully",
            "Changed files are listed",
            "Validation command and result are reported",
        ],
        "risk_notes": ["Approval is controlled by the selected backlog item."],
        "prompts": {
            "execution_brief_template": (
                "Request:\n{request}\n\n"
                "Relevant files:\n{relevant_files}\n\n"
                "Constraints:\n{constraint_list}\n\n"
                "Acceptance criteria:\n{acceptance_criteria}\n\n"
                "Approval reason:\n{approval_reason}\n\n"
                "Risk notes:\n{risk_notes}"
            ),
            "agent_instruction_template": (
                "Task:\n{request}\n\n"
                "Brief:\n{brief}\n\n"
                "Approval:\n"
                "Needs approval: {needs_approval}\n"
                "Approval reason: {approval_reason}\n"
                "Approved: {approved}\n\n"
                "Runtime limit:\n{max_runtime_minutes} minutes\n\n"
                "Allowed directories:\n{allowed_directories}\n\n"
                "Instruction:\n"
                "Complete this backlog task inside the allowed directories. "
                "Keep the change small, report changed files, and report the exact "
                "validation command and result."
            ),
        },
    }
