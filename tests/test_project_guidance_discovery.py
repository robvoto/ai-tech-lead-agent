from __future__ import annotations

from pathlib import Path

from ai_tech_lead.project_guidance_discovery import (
    _SKILL_INDEX_ENTRY_PATTERN,
    discover_project_guidance,
)


def test_skill_index_entry_pattern_accepts_both_real_bullet_shapes() -> None:
    backtick = _SKILL_INDEX_ENTRY_PATTERN.match(
        "- `code-change/SKILL.md` - code, runtime, test, or integration changes."
    )
    colon = _SKILL_INDEX_ENTRY_PATTERN.match(
        "- code-change/SKILL.md: code, tests, validation, or runtime changes."
    )
    assert backtick is not None
    assert colon is not None
    assert (backtick.group("backtick_path"), backtick.group("colon_path")) == (
        "code-change/SKILL.md",
        None,
    )
    assert (colon.group("colon_path"), colon.group("backtick_path")) == (
        "code-change/SKILL.md",
        None,
    )


def test_skill_index_entry_pattern_rejects_headings_and_prose() -> None:
    assert _SKILL_INDEX_ENTRY_PATTERN.match("## Skills") is None
    assert _SKILL_INDEX_ENTRY_PATTERN.match("Platform skills, pick one:") is None
    assert _SKILL_INDEX_ENTRY_PATTERN.match("") is None


def test_no_project_pack_returns_empty(tmp_path: Path) -> None:
    assert discover_project_guidance("do the thing", str(tmp_path)) == ()


def test_empty_project_root_returns_empty() -> None:
    assert discover_project_guidance("do the thing", "") == ()


def test_nonexistent_project_root_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert discover_project_guidance("do the thing", str(missing)) == ()


def test_agents_md_is_included_when_present(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# AGENTS.md\n\nUse docs/INDEX.md for routing.", encoding="utf-8"
    )

    notes = discover_project_guidance("do the thing", str(tmp_path))

    assert any("AGENTS.md" in note and "routing" in note for note in notes)


def test_docs_index_is_included_when_present(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n- `ARCHITECTURE.md` - system boundaries.", encoding="utf-8"
    )

    notes = discover_project_guidance("do the thing", str(tmp_path))

    assert any("docs/INDEX.md" in note and "system boundaries" in note for note in notes)


def test_docs_index_loads_bounded_referenced_architecture_for_ownership_request(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n"
        "- `ARCHITECTURE.md` - system architecture, module boundaries, adapter strategy.\n"
        "- `GRAPH_WORKFLOW.md` - implemented workflow and execution boundary.\n",
        encoding="utf-8",
    )
    (docs_dir / "ARCHITECTURE.md").write_text(
        "# Architecture\n\n"
        "AI Tech Lead owns the specialist-internal graph and maps internal pauses "
        "to the Agent Hub subprocess contract.",
        encoding="utf-8",
    )
    (docs_dir / "GRAPH_WORKFLOW.md").write_text(
        "# Workflow\n\nExecution routing details.", encoding="utf-8"
    )

    notes = discover_project_guidance(
        "Summarize the ownership boundary between AI Tech Lead and Agent Hub",
        str(tmp_path),
    )

    architecture = next(note for note in notes if note.startswith("docs/ARCHITECTURE.md:"))
    assert "Agent Hub subprocess contract" in architecture


def test_duplicate_doc_reference_keeps_the_most_relevant_index_description(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n"
        "- `ARCHITECTURE.md` - general system architecture.\n"
        "- `OTHER.md` - ownership notes.\n"
        "- `ARCHITECTURE.md` also contains the Agent Hub subprocess boundary.\n",
        encoding="utf-8",
    )
    (docs_dir / "ARCHITECTURE.md").write_text("ARCHITECTURE DETAIL", encoding="utf-8")
    (docs_dir / "OTHER.md").write_text("OTHER DETAIL", encoding="utf-8")

    notes = discover_project_guidance("Agent Hub subprocess boundary", str(tmp_path))
    followed = [
        note for note in notes if note.startswith("docs/") and not note.startswith("docs/INDEX.md:")
    ]

    assert followed[0].startswith("docs/ARCHITECTURE.md:")


def test_referenced_doc_excerpt_can_reach_relevant_content_late_in_file(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n- `ARCHITECTURE.md` - system boundaries.\n",
        encoding="utf-8",
    )
    filler = "\n".join(f"Unrelated introduction {i}." for i in range(40))
    (docs_dir / "ARCHITECTURE.md").write_text(
        filler
        + "\nAn external caller owns caller-side orchestration and resume UX."
        + "\nAI Tech Lead owns the specialist-internal graph and maps pauses to the Agent Hub subprocess contract.\n",
        encoding="utf-8",
    )

    notes = discover_project_guidance("AI Tech Lead Agent Hub ownership", str(tmp_path))
    architecture = next(note for note in notes if note.startswith("docs/ARCHITECTURE.md:"))

    assert "external caller owns caller-side orchestration" in architecture
    assert "owns the specialist-internal graph" in architecture
    assert "Agent Hub subprocess contract" in architecture


def test_docs_index_reference_cannot_escape_docs_directory(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n- `../PRIVATE.md` - private material.\n",
        encoding="utf-8",
    )
    (tmp_path / "PRIVATE.md").write_text("DO NOT LOAD", encoding="utf-8")

    notes = discover_project_guidance("private material", str(tmp_path))

    assert not any("DO NOT LOAD" in note for note in notes)
    assert not any(note.startswith("PRIVATE.md:") for note in notes)


def test_docs_index_reference_loading_is_capped_at_two_documents(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n"
        "- `ONE.md` - alpha reference.\n"
        "- `TWO.md` - beta reference.\n"
        "- `THREE.md` - gamma reference.\n",
        encoding="utf-8",
    )
    for name in ("ONE", "TWO", "THREE"):
        (docs_dir / f"{name}.md").write_text(f"# {name}\n\n{name} DETAIL", encoding="utf-8")

    notes = discover_project_guidance("alpha beta gamma", str(tmp_path))
    followed = [
        note for note in notes if note.startswith("docs/") and not note.startswith("docs/INDEX.md:")
    ]

    assert len(followed) == 2


def test_skills_index_scores_and_drills_into_the_best_match_only(tmp_path: Path) -> None:
    """Uses deliberately arbitrary, non-AI-Tech-Lead skill names to prove nothing
    is hardcoded — relevance comes only from scoring the index's own text."""
    skills_dir = tmp_path / ".agents" / "skills"
    (skills_dir / "widget-forging").mkdir(parents=True)
    (skills_dir / "widget-forging" / "SKILL.md").write_text(
        "Widget forging detail: temper the alloy before shaping.", encoding="utf-8"
    )
    (skills_dir / "sprocket-tuning").mkdir(parents=True)
    (skills_dir / "sprocket-tuning" / "SKILL.md").write_text(
        "Sprocket tuning detail: align teeth before final torque.", encoding="utf-8"
    )
    (skills_dir / "INDEX.md").write_text(
        "# Skills Index\n\n"
        "## Skills\n\n"
        "- `widget-forging/SKILL.md` - widget forging, alloy tempering, shaping.\n"
        "- `sprocket-tuning/SKILL.md` - sprocket tuning, gear alignment, torque.\n",
        encoding="utf-8",
    )

    notes = discover_project_guidance("Please retemper the widget alloy", str(tmp_path))

    # notes[0] is the raw index dump (legitimately lists both skills); the
    # drill-down note (notes[1]) must contain only the winning skill's content.
    assert len(notes) == 2
    assert "widget-forging/SKILL.md" in notes[1]
    assert "temper the alloy before shaping" in notes[1]
    assert "align teeth before final torque" not in notes[1]


def test_skills_index_included_even_with_no_scoring_match(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    (skills_dir / "widget-forging").mkdir(parents=True)
    (skills_dir / "widget-forging" / "SKILL.md").write_text("detail", encoding="utf-8")
    (skills_dir / "INDEX.md").write_text(
        "# Skills Index\n\n## Skills\n\n- `widget-forging/SKILL.md` - widget forging.\n",
        encoding="utf-8",
    )

    notes = discover_project_guidance("completely unrelated grocery list request", str(tmp_path))

    # Only the index note — nothing scored above zero, so no drill-down note.
    assert len(notes) == 1
    assert ".agents/skills/INDEX.md" in notes[0]


def test_missing_skill_file_referenced_by_index_is_skipped(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "INDEX.md").write_text(
        "# Skills Index\n\n## Skills\n\n- `ghost-skill/SKILL.md` - a skill file that isn't there.\n",
        encoding="utf-8",
    )

    notes = discover_project_guidance("ghost skill request", str(tmp_path))

    # Only the index note itself — no drill-down note, since the referenced
    # skill file doesn't actually exist on disk.
    assert len(notes) == 1
    assert ".agents/skills/INDEX.md" in notes[0]


def test_content_is_bounded_even_for_a_very_long_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 10_000, encoding="utf-8")

    notes = discover_project_guidance("do the thing", str(tmp_path))

    assert len(notes) == 1
    assert len(notes[0]) < 1_000


# ---------------------------------------------------------------------------
# Real-project-shape regression fixtures.
#
# These mirror three real repos this discovery mechanism was proven against
# during ATL-077's cross-project proof (Job Hunter, Agent Factory, and a
# genuinely instruction-light real repo) — not the real repos themselves,
# since a test referencing an absolute path on one machine isn't portable to
# CI or another developer's checkout. The shapes (not the project names or
# content) are what matter and are reproduced here:
#   - AGENTS.md + docs/INDEX.md, no .agents/skills/INDEX.md at all.
#   - AGENTS.md + docs/INDEX.md + .agents/skills/INDEX.md using a plain
#     "- path.md: summary" bullet shape (no backticks) — discovered live
#     against Agent Factory's real `.agents/skills/INDEX.md`, which the original
#     backtick-only parser could not read at all.
#   - No project pack whatsoever.
# ---------------------------------------------------------------------------


def _write_project_context_only_fixture(root: Path) -> None:
    """Shape: AGENTS.md + docs/INDEX.md, no skills index (e.g. Job Hunter)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(
        "# Agent Instructions\n\nAlways-loaded agent loader. Keep this file small.\n"
        "Load the relevant skill from `.agents/skills/`.",
        encoding="utf-8",
    )
    docs_dir = root / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n"
        "- [ARCHITECTURE.md](ARCHITECTURE.md) — runtime layers and module ownership.\n",
        encoding="utf-8",
    )


def _write_colon_skills_index_fixture(root: Path) -> None:
    """Shape: full project pack, but .agents/skills/INDEX.md uses a plain colon bullet
    (no backticks) — e.g. Agent Factory's real `.agents/skills/INDEX.md`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(
        "# AGENTS.md\n\nMinimal always-loaded routing instructions.", encoding="utf-8"
    )
    docs_dir = root / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "INDEX.md").write_text("# Documentation Index\n\nRoute here first.", encoding="utf-8")
    skills_dir = root / ".agents" / "skills"
    (skills_dir / "code-change").mkdir(parents=True)
    (skills_dir / "code-change" / "SKILL.md").write_text(
        "# Code Change Skill\n\nFor code changes: change only files required by the task.",
        encoding="utf-8",
    )
    (skills_dir / "agent-authoring").mkdir(parents=True)
    (skills_dir / "agent-authoring" / "SKILL.md").write_text(
        "# Agent Authoring Skill\n\nDesign and stage agent packages for registry discovery.",
        encoding="utf-8",
    )
    (skills_dir / "INDEX.md").write_text(
        "# Skills Index\n\n"
        "- code-change/SKILL.md: code, tests, validation, or runtime changes.\n"
        "- agent-authoring/SKILL.md: design and stage agent packages; registry discovery fields.\n",
        encoding="utf-8",
    )


def test_colon_style_skills_index_is_parsed_like_the_backtick_style(tmp_path: Path) -> None:
    """Regression for the real Agent Factory shape: a `.agents/skills/INDEX.md` using
    `- path.md: summary` bullets (no backticks) must still drive selection."""
    root = tmp_path / "colon-shape-project"
    _write_colon_skills_index_fixture(root)

    notes = discover_project_guidance("Fix a typo in a code comment", str(root))

    assert any("code-change/SKILL.md" in note for note in notes)
    assert any("change only files required by the task" in note for note in notes)
    assert not any("agent-authoring" in note and "Design and stage" in note for note in notes)


def test_colon_style_skills_index_selects_a_different_skill_for_a_different_task(
    tmp_path: Path,
) -> None:
    """Same project, same skills index, a different task — proves selection is
    re-evaluated per request rather than fixed once discovered."""
    root = tmp_path / "colon-shape-project"
    _write_colon_skills_index_fixture(root)

    notes = discover_project_guidance(
        "Stage a new agent package and register it for discovery", str(root)
    )

    assert any("agent-authoring/SKILL.md" in note for note in notes)
    assert any("Design and stage agent packages" in note for note in notes)
    assert not any("code-change/SKILL.md" in note and "change only files" in note for note in notes)


def test_project_context_only_shape_never_drills_into_a_skill(tmp_path: Path) -> None:
    """Regression for the real Job Hunter shape: AGENTS.md + docs/INDEX.md with
    no .agents/skills/INDEX.md must include only those two, never fabricate a skill."""
    root = tmp_path / "context-only-project"
    _write_project_context_only_fixture(root)

    notes = discover_project_guidance("Update the CSS spacing on the dashboard", str(root))

    assert len(notes) == 2
    assert any(note.startswith("AGENTS.md:") for note in notes)
    assert any(note.startswith("docs/INDEX.md:") for note in notes)


def test_instruction_light_real_shape_yields_nothing_and_does_not_block(tmp_path: Path) -> None:
    """Regression for a genuinely instruction-light real repo: no AGENTS.md, no
    docs index, no skills index — must degrade to nothing, not an error."""
    root = tmp_path / "instruction-light-project"
    root.mkdir()
    (root / "README.md").write_text("Just a README, nothing else.", encoding="utf-8")

    notes = discover_project_guidance("Fix a typo in a code comment", str(root))

    assert notes == ()


def test_cross_project_isolation_across_the_three_real_shapes(tmp_path: Path) -> None:
    """The three real shapes proven live (project-context-only, colon-style
    skills index, instruction-light), checked back-to-back, must never leak
    into each other."""
    context_only_root = tmp_path / "context-only"
    colon_skills_root = tmp_path / "colon-skills"
    instruction_light_root = tmp_path / "instruction-light"

    _write_project_context_only_fixture(context_only_root)
    _write_colon_skills_index_fixture(colon_skills_root)
    instruction_light_root.mkdir()

    request = "Fix a typo in a code comment"
    context_only_notes = discover_project_guidance(request, str(context_only_root))
    colon_skills_notes = discover_project_guidance(request, str(colon_skills_root))
    instruction_light_notes = discover_project_guidance(request, str(instruction_light_root))

    assert not any("code-change" in note for note in context_only_notes)
    assert not any("Always-loaded agent loader" in note for note in colon_skills_notes)
    assert instruction_light_notes == ()
