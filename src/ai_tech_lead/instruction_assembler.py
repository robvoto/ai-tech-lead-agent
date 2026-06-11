"""Build compact handoff text for the coding agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.prompt_loader import load_prompt

AGENTS_PATH = PROJECT_ROOT / "AGENTS.md"
IDENTITY_PATH = PROJECT_ROOT / "docs" / "ORCHESTRATOR_IDENTITY.md"
SKILLS_DIR = PROJECT_ROOT / ".skills"


@dataclass(frozen=True)
class SkillSelection:
    name: str
    path: Path
    reason: str


def build_agent_instruction(
    *,
    request: str,
    brief: str,
    formulated_task: str,
    task_feedback: list[str],
    needs_approval: bool,
    approval_reason: str,
    approved: bool,
    settings: AppSettings,
    research_sources: list[str] | None = None,
) -> str:
    skills = select_skills(request)
    selected_skills = "\n\n".join(_render_skill(skill) for skill in skills)
    project_rules = _extract_project_rules()
    backlog_ownership_rules = load_prompt("backlog_ownership_rules.md")
    coding_agent_handoff_rules = load_prompt("coding_agent_handoff_rules.md")
    stop_conditions = _format_bullets(
        [
            "Stop if files outside the allowed directories are needed.",
            "Stop if the task conflicts with project rules.",
            "Stop if validation cannot be run or cannot be explained.",
        ]
    )
    final_instruction = (
        "Complete only this task. Keep the change small, report changed files, "
        "explain why the design is appropriate, and report the exact validation "
        "command and result."
    )

    sections = [
        "# Coding-Agent Handoff",
        _section("Task", formulated_task or request),
        _section("Execution brief", brief),
    ]
    if research_sources:
        sections.append(
            _section(
                "Evidence Sources",
                "Local research notes consulted for this task:\n" + _format_bullets(research_sources),
            )
        )
    if task_feedback:
        sections.append(
            _section(
                "Task feedback",
                "Apply this only to the current task.\n" + _format_bullets(task_feedback),
            )
        )
    sections.extend(
        [
            _section(
                "Approval state",
                f"Needs approval: {needs_approval}\nApproval reason: {approval_reason}\nApproved: {approved}",
            ),
            _section("Orchestrator identity", _load_orchestrator_identity()),
            _section("Project rules", project_rules),
            _section("Selected skills", selected_skills),
            _section("Allowed directories", _format_bullets(settings.allowed_directories)),
            _section("Runtime limit", f"{settings.max_runtime_minutes} minutes"),
            _section("Acceptance criteria", _format_bullets(settings.acceptance_criteria)),
            _section("Backlog ownership rules", backlog_ownership_rules),
            _section("Coding-agent handoff rules", coding_agent_handoff_rules),
            _section("Stop conditions", stop_conditions),
            _section("Final instruction", final_instruction),
        ]
    )
    return "\n\n".join(sections)


def select_skills(request: str) -> list[SkillSelection]:
    text = request.lower()
    selections: list[SkillSelection] = []
    if _has_any(text, ["code", "test", "runtime", "graph", "telegram", "admin", "settings", "subprocess", "codex", "python"]):
        selections.append(_skill("code-change", "Task changes code or runtime behaviour."))
    if _has_any(text, ["backlog", "jh-", "task id", "approval required"]):
        selections.append(_skill("backlog-management", "Task touches backlog selection or task handoff."))
    if _has_any(text, ["agents.md", ".skills", "skill.md", "handoff", "project rules"]):
        selections.append(_skill("instruction-maintenance", "Task edits or relies on project rule files."))
    if not selections:
        selections.append(_skill("code-change", "Default for implementation work."))
    return _dedupe(selections)


def _extract_project_rules() -> str:
    content = AGENTS_PATH.read_text(encoding="utf-8")
    return "\n\n".join(
        _extract_section(content, heading)
        for heading in ["Default workflow", "Navigation", "Universal rules", "Finish report"]
    )


def _load_orchestrator_identity() -> str:
    if not IDENTITY_PATH.exists():
        raise FileNotFoundError(f"Orchestrator identity file not found: {IDENTITY_PATH}")
    return IDENTITY_PATH.read_text(encoding="utf-8").strip()


def _extract_section(content: str, heading: str) -> str:
    marker = f"## {heading}"
    start = content.find(marker)
    if start == -1:
        raise ValueError(f"AGENTS.md is missing section: {heading}")
    next_start = content.find("\n## ", start + len(marker))
    if next_start == -1:
        return content[start:].strip()
    return content[start:next_start].strip()


def _skill(name: str, reason: str) -> SkillSelection:
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"Selected skill file not found: {path}")
    return SkillSelection(name=name, path=path, reason=reason)


def _render_skill(selection: SkillSelection) -> str:
    skill_content = selection.path.read_text(encoding="utf-8").strip()
    return f"## {selection.name}\nReason: {selection.reason}\n{skill_content}"


def _dedupe(selections: list[SkillSelection]) -> list[SkillSelection]:
    seen: set[str] = set()
    result: list[SkillSelection] = []
    for selection in selections:
        if selection.name not in seen:
            result.append(selection)
            seen.add(selection.name)
    return result


def _has_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}"


def _format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)
