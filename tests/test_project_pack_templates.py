from __future__ import annotations

from pathlib import Path

import pytest

from ai_tech_lead.project_pack_templates import bootstrap_project_pack, project_pack_templates


def test_project_pack_templates_include_expected_starter_files() -> None:
    templates = project_pack_templates()

    assert Path("AGENTS.md") in templates
    assert Path("docs/INDEX.md") in templates
    assert Path(".agents/skills/INDEX.md") in templates
    assert Path(".agents/skills/code-change/SKILL.md") in templates
    assert Path(".agents/skills/instruction-maintenance/SKILL.md") in templates


def test_bootstrap_project_pack_writes_starter_files(tmp_path: Path) -> None:
    lines = bootstrap_project_pack(tmp_path)

    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "docs" / "INDEX.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "INDEX.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "code-change" / "SKILL.md").is_file()
    assert "Bootstrapped starter project pack into" in lines[0]
    assert "review the starter AGENTS.md" in lines[-1]


def test_bootstrap_project_pack_refuses_to_overwrite_without_flag(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="would overwrite existing files"):
        bootstrap_project_pack(tmp_path)


def test_bootstrap_project_pack_can_overwrite_when_explicitly_allowed(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("existing", encoding="utf-8")

    bootstrap_project_pack(tmp_path, overwrite=True)

    assert "Purpose: minimal always-loaded repository instructions" in (
        tmp_path / "AGENTS.md"
    ).read_text(encoding="utf-8")
