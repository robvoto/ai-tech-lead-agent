from __future__ import annotations

from pathlib import Path

from ai_tech_lead.project_guidance_discovery import discover_project_guidance


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


def test_skills_index_scores_and_drills_into_the_best_match_only(tmp_path: Path) -> None:
    """Uses deliberately arbitrary, non-AI-Tech-Lead skill names to prove nothing
    is hardcoded — relevance comes only from scoring the index's own text."""
    skills_dir = tmp_path / ".skills"
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
    skills_dir = tmp_path / ".skills"
    (skills_dir / "widget-forging").mkdir(parents=True)
    (skills_dir / "widget-forging" / "SKILL.md").write_text("detail", encoding="utf-8")
    (skills_dir / "INDEX.md").write_text(
        "# Skills Index\n\n## Skills\n\n- `widget-forging/SKILL.md` - widget forging.\n",
        encoding="utf-8",
    )

    notes = discover_project_guidance("completely unrelated grocery list request", str(tmp_path))

    # Only the index note — nothing scored above zero, so no drill-down note.
    assert len(notes) == 1
    assert ".skills/INDEX.md" in notes[0]


def test_missing_skill_file_referenced_by_index_is_skipped(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".skills"
    skills_dir.mkdir()
    (skills_dir / "INDEX.md").write_text(
        "# Skills Index\n\n## Skills\n\n- `ghost-skill/SKILL.md` - a skill file that isn't there.\n",
        encoding="utf-8",
    )

    notes = discover_project_guidance("ghost skill request", str(tmp_path))

    # Only the index note itself — no drill-down note, since the referenced
    # skill file doesn't actually exist on disk.
    assert len(notes) == 1
    assert ".skills/INDEX.md" in notes[0]


def test_content_is_bounded_even_for_a_very_long_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 10_000, encoding="utf-8")

    notes = discover_project_guidance("do the thing", str(tmp_path))

    assert len(notes) == 1
    assert len(notes[0]) < 1_000
