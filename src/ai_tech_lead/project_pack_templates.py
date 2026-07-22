"""Starter project-pack templates for target repositories."""

from __future__ import annotations

from pathlib import Path


def bootstrap_project_pack(
    target_root: Path,
    *,
    overwrite: bool = False,
) -> list[str]:
    """Write a small starter project pack into ``target_root``.

    This is an explicit operator action. It refuses to overwrite any existing
    target file unless ``overwrite`` is true.
    """

    normalized_root = Path(target_root).resolve()
    files = project_pack_templates()
    conflicts = [
        normalized_root / relative_path
        for relative_path in files
        if (normalized_root / relative_path).exists()
    ]
    if conflicts and not overwrite:
        conflict_list = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(
            "Project-pack bootstrap would overwrite existing files. "
            f"Re-run with overwrite enabled only after review: {conflict_list}"
        )

    written_paths: list[Path] = []
    for relative_path, content in files.items():
        destination = normalized_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        written_paths.append(destination)

    lines = [f"Bootstrapped starter project pack into {normalized_root}."]
    lines.extend(f"Wrote: {path}" for path in written_paths)
    lines.append(
        "Next step: review the starter AGENTS.md, docs/INDEX.md, and .skills files "
        "before using this repo as a coding target."
    )
    return lines


def project_pack_templates() -> dict[Path, str]:
    """Return the starter project-pack file set keyed by relative path."""

    return {
        Path("AGENTS.md"): _agents_template(),
        Path("docs/INDEX.md"): _docs_index_template(),
        Path(".skills/INDEX.md"): _skills_index_template(),
        Path(".skills/code-change/SKILL.md"): _code_change_skill_template(),
        Path(".skills/instruction-maintenance/SKILL.md"): _instruction_skill_template(),
    }


def _agents_template() -> str:
    return (
        "# AGENTS.md\n\n"
        "Purpose: minimal always-loaded repository instructions for AI agents "
        "working in this project.\n\n"
        "This file is a routing layer only. It is not the full project manual.\n\n"
        "## Default workflow\n\n"
        "1. Use `docs/INDEX.md` to find the smallest relevant project document.\n"
        "2. Use `.skills/INDEX.md` to choose one relevant task skill.\n"
        "3. Inspect the current files before giving code-specific advice or editing.\n"
        "4. Keep work bounded to the smallest file set that solves the task.\n\n"
        "## Navigation\n\n"
        "- Project docs: `docs/INDEX.md`\n"
        "- Task skills: `.skills/INDEX.md`\n\n"
        "## Universal rules\n\n"
        "- Never guess local architecture or commands.\n"
        "- Do not broaden scope silently.\n"
        "- Prefer small validated changes.\n"
        "- Stop and ask before destructive, ambiguous, or expensive actions.\n\n"
        "## Finish report\n\n"
        "- Files changed\n"
        "- Behaviour changed\n"
        "- Validation run\n"
        "- Remaining risk or follow-up\n"
    )


def _docs_index_template() -> str:
    return (
        "# Documentation Index\n\n"
        "Use this file as the single documentation entry point. Start here, "
        "then open only the document needed for the current task.\n\n"
        "## Core project documents\n\n"
        "- `ARCHITECTURE.md` - system boundaries, major modules, and integration points.\n"
        "- `RUNTIME_RUNBOOK.md` - run, test, and troubleshooting commands.\n"
        "- `CONTEXT_MANAGEMENT.md` - what context is safe to expose to agents, logs, or tools.\n\n"
        "## Entry points\n\n"
        "- Root `AGENTS.md` - minimal always-loaded routing file.\n"
        "- `.skills/INDEX.md` - project skill catalogue.\n\n"
        "## Maintenance rules\n\n"
        "- Keep this index factual and short.\n"
        "- Add durable docs here.\n"
        "- Do not paste long explanations into this index.\n"
    )


def _skills_index_template() -> str:
    return (
        "# Skills Index\n\n"
        "Use this file to choose one scoped skill for the current task. "
        "Do not load every skill by default.\n\n"
        "## Selection rule\n\n"
        "Pick the smallest skill that matches the requested outcome or files being changed.\n\n"
        "## Skills\n\n"
        "- `code-change/SKILL.md` - code, tests, runtime, or integration changes.\n"
        "- `instruction-maintenance/SKILL.md` - AGENTS, docs index, or skill maintenance.\n\n"
        "## Maintenance rules\n\n"
        "- Add a skill here when a new `.skills/<name>/SKILL.md` is created.\n"
        "- Keep descriptions short enough to support routing only.\n"
        "- Put detailed task rules inside the skill itself, not in this index.\n"
    )


def _code_change_skill_template() -> str:
    return (
        "---\n"
        "name: code-change\n"
        "description: Use for code, tests, runtime, or integration implementation changes.\n"
        "---\n\n"
        "# Skill: Code Change\n\n"
        "Use before modifying existing code.\n\n"
        "## Rules\n\n"
        "- Inspect the target file before editing.\n"
        "- Keep changes small and scoped.\n"
        "- Touch only files required for the task.\n"
        "- Prefer clear failure over silent fallback behavior.\n"
        "- Record exact validation evidence before claiming the task is done.\n"
    )


def _instruction_skill_template() -> str:
    return (
        "---\n"
        "name: instruction-maintenance\n"
        "description: Use when editing AGENTS.md, skills, or other project instruction files.\n"
        "---\n\n"
        "# Skill: Instruction Maintenance\n\n"
        "Use when editing project instructions.\n\n"
        "## Rules\n\n"
        "- Put universal operational rules in `AGENTS.md`.\n"
        "- Put repeatable procedures in `.skills`.\n"
        "- Put human explanations and rationale in `docs`.\n"
        "- Keep instruction files small, current, and non-contradictory.\n"
    )
