"""Build compact handoff text for the coding agent."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.prompt_loader import BACKLOG_OWNERSHIP_RULES_PROMPT_KEY, load_prompt
from ai_tech_lead.target_project_context import TargetProjectContext

RUNTIME_CORE_DIR = PROJECT_ROOT / "runtime_core"


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
    approved: bool,
    settings: AppSettings,
    target_project_context: TargetProjectContext | None = None,
    project_root: Path | None = None,
    research_evidence: list[str] | None = None,
    agent_correction: str | None = None,
    project_guidance: list[str] | None = None,
) -> str:
    if target_project_context is not None and target_project_context.has_explicit_context:
        target_project_root = target_project_context.require_project_root(
            "coding-agent instruction assembly"
        )
    else:
        target_project_root = project_root or Path(settings.project_root)
    core_instruction = _load_runtime_core_instruction()
    core_skills = select_runtime_core_skills(request)
    skills = select_skills(request, project_root=target_project_root)
    selected_core_skills = "\n\n".join(_render_skill(skill) for skill in core_skills)
    selected_skills = "\n\n".join(_render_skill(skill) for skill in skills)
    project_rules = _extract_project_rules(target_project_root)
    backlog_ownership_rules = load_prompt(BACKLOG_OWNERSHIP_RULES_PROMPT_KEY)
    stop_conditions = _format_bullets(
        [
            "Stop if files outside the allowed directories are needed.",
            "Stop if the task conflicts with project rules.",
            "Stop if validation cannot be run or cannot be explained.",
            "If this task appears to already be implemented in the codebase, "
            "reply 'ALREADY_DONE: [reason]' and stop without making changes.",
        ]
    )
    final_instruction = (
        "Complete only this task. Keep the change small, report changed files, "
        "explain why the design is appropriate, and report the exact validation "
        "command and result."
    )

    project_context = _format_bullets(settings.project_context)
    sections = [
        "# Coding-Agent Handoff",
        _section("Task", formulated_task or request),
        _section("Execution brief", brief),
        _section("Project context", project_context),
        _section("AI Tech Lead core", core_instruction),
        _section("Reusable runtime skills", selected_core_skills),
    ]
    if research_evidence:
        sections.append(
            _section(
                "Research evidence",
                "Relevant local and official documentation consulted for this task:\n"
                + _format_bullets(research_evidence),
            )
        )
    if task_feedback:
        sections.append(
            _section(
                "Task feedback",
                "Apply this only to the current task.\n" + _format_bullets(task_feedback),
            )
        )
    if agent_correction:
        sections.append(
            _section(
                "Previous attempt failed",
                "The previous coding agent run failed. Diagnose and resolve the issue before "
                "proceeding:\n" + agent_correction,
            )
        )
    if project_guidance:
        sections.append(
            _section(
                "Selected project guidance (same as Tech Lead Analysis and plan review)",
                _format_bullets(project_guidance),
            )
        )
    sections.extend(
        [
            _section(
                "Approval state",
                f"Needs approval: {needs_approval}\nApproved: {approved}",
            ),
            _section("Project rules", project_rules),
            _section("Selected skills", selected_skills),
            _section("Allowed directories", _format_bullets(settings.allowed_directories)),
            _section("Runtime limit", f"{settings.max_runtime_minutes} minutes"),
            _section("Backlog ownership rules", backlog_ownership_rules),
            _section("Stop conditions", stop_conditions),
            _section("Final instruction", final_instruction),
        ]
    )
    return "\n\n".join(sections)


def selected_instruction_files(
    request: str,
    *,
    project_root: Path | None = None,
) -> tuple[Path, ...]:
    """Return the bounded instruction files actually selected for a handoff.

    This reports paths only. Audit callers hash the files without copying their
    contents, so replay metadata does not become a second instruction store.
    """

    target_project_root = project_root or PROJECT_ROOT
    core_path = RUNTIME_CORE_DIR / "CORE.md"
    if not core_path.is_file():
        raise FileNotFoundError(f"Runtime core instruction file not found: {core_path}")
    selections = [
        core_path,
        *(selection.path for selection in select_runtime_core_skills(request)),
        *(selection.path for selection in select_skills(request, project_root=target_project_root)),
    ]
    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in selections:
        resolved = path.resolve()
        if resolved not in seen:
            unique_paths.append(resolved)
            seen.add(resolved)
    return tuple(unique_paths)


def selected_instruction_hashes(
    request: str,
    *,
    project_root: Path | None = None,
) -> dict[str, str]:
    """Return SHA-256 versions for the selected instruction files only."""

    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in selected_instruction_files(request, project_root=project_root)
    }


def select_skills(
    request: str,
    *,
    project_root: Path | None = None,
) -> list[SkillSelection]:
    target_project_root = project_root or PROJECT_ROOT
    _require_skills_index(target_project_root)
    text = request.lower()
    selections: list[SkillSelection] = []
    if _has_any(
        text,
        [
            "code",
            "test",
            "runtime",
            "graph",
            "telegram",
            "admin",
            "settings",
            "subprocess",
            "codex",
            "python",
        ],
    ):
        selections.append(
            _skill(
                "code-change",
                "Task changes code or runtime behaviour.",
                project_root=target_project_root,
            )
        )
    if _has_any(text, ["backlog", "jh-", "task id", "approval required"]):
        selections.append(
            _skill(
                "backlog-management",
                "Task touches backlog selection or task handoff.",
                project_root=target_project_root,
            )
        )
    if _has_any(text, ["agents.md", ".skills", "skill.md", "handoff", "project rules"]):
        selections.append(
            _skill(
                "instruction-maintenance",
                "Task edits or relies on project rule files.",
                project_root=target_project_root,
            )
        )
    if not selections:
        selections.append(
            _skill(
                "code-change",
                "Default for implementation work.",
                project_root=target_project_root,
            )
        )
    return _dedupe(selections)


def select_runtime_core_skills(request: str) -> list[SkillSelection]:
    text = request.lower()
    selections = [
        _runtime_core_skill(
            "bounded-delivery",
            "Core runtime rule for small, validated, bounded delivery.",
        ),
        _runtime_core_skill(
            "result-reporting",
            "Core runtime rule for machine-readable, reviewable completion reporting.",
        ),
    ]
    if _has_any(
        text,
        [
            "research",
            "docs",
            "approval",
            "risk",
            "langgraph",
            "runtime",
            "architecture",
        ],
    ):
        selections.append(
            _runtime_core_skill(
                "research-and-approval",
                "Core runtime rule for bounded research and approval discipline.",
            )
        )
    return _dedupe(selections)


def _extract_project_rules(project_root: Path) -> str:
    agents_path = project_root / "AGENTS.md"
    content = agents_path.read_text(encoding="utf-8")
    return "\n\n".join(
        _extract_section(content, heading)
        for heading in [
            "Default workflow",
            "Navigation",
            "Universal rules",
            "Finish report",
        ]
    )


def _extract_section(content: str, heading: str) -> str:
    marker = f"## {heading}"
    start = content.find(marker)
    if start == -1:
        raise ValueError(f"AGENTS.md is missing section: {heading}")
    next_start = content.find("\n## ", start + len(marker))
    if next_start == -1:
        return content[start:].strip()
    return content[start:next_start].strip()


def _skills_dir(project_root: Path) -> Path:
    return project_root / ".skills"


def _runtime_core_skills_dir() -> Path:
    return RUNTIME_CORE_DIR / "skills"


def _require_skills_index(project_root: Path) -> None:
    index_path = _skills_dir(project_root) / "INDEX.md"
    if not index_path.is_file():
        raise FileNotFoundError(f"Target project skills index not found: {index_path}")


def _skill(name: str, reason: str, *, project_root: Path) -> SkillSelection:
    path = _skills_dir(project_root) / name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"Selected target-project skill file not found: {path}")
    return SkillSelection(name=name, path=path, reason=reason)


def _load_runtime_core_instruction() -> str:
    path = RUNTIME_CORE_DIR / "CORE.md"
    if not path.is_file():
        raise FileNotFoundError(f"Runtime core instruction file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _runtime_core_skill(name: str, reason: str) -> SkillSelection:
    path = _runtime_core_skills_dir() / name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"Runtime core skill file not found: {path}")
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
