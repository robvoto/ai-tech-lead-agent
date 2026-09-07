from __future__ import annotations

from pathlib import Path

from ai_tech_lead.validation_command_discovery import discover_validation_command


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_documented_command_in_agents_md_wins(tmp_path: Path) -> None:
    _write(
        tmp_path / "AGENTS.md",
        "# AGENTS\n\nValidation command: `uv run pytest -q`\n\nOther rules follow.\n",
    )
    # An inferable signal is also present, but the documented line takes priority.
    _write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\n")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")

    result = discover_validation_command("add a feature", str(tmp_path))

    assert result is not None
    assert result.command == "uv run pytest -q"
    assert result.source == "documented: AGENTS.md"


def test_documented_command_found_in_best_matching_skill(tmp_path: Path) -> None:
    _write(
        tmp_path / ".skills" / "INDEX.md",
        "# Skills\n\n- `code-change/SKILL.md` - code and test implementation changes.\n",
    )
    _write(
        tmp_path / ".skills" / "code-change" / "SKILL.md",
        "# Code Change\n\n## Testing\nUse `uv run pytest` as the core validation command.\n",
    )

    result = discover_validation_command("make a code change with tests", str(tmp_path))

    assert result is not None
    assert result.command == "uv run pytest"
    assert result.source.startswith("documented: .skills/")


def test_single_inferred_signal_is_used(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", '{"scripts": {"test": "vitest run"}}')

    result = discover_validation_command("fix the ui", str(tmp_path))

    assert result is not None
    assert result.command == "npm test"
    assert result.source == "inferred: package.json test script"


def test_conflicting_signals_return_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\n")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    _write(tmp_path / "package.json", '{"scripts": {"test": "vitest run"}}')

    assert discover_validation_command("do something", str(tmp_path)) is None


def test_no_signal_returns_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "Just a readme, no test tooling declared.\n")

    assert discover_validation_command("do something", str(tmp_path)) is None


def test_unsafe_documented_command_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path / "AGENTS.md",
        "Validation command: `pytest && rm -rf /`\n",
    )

    assert discover_validation_command("anything", str(tmp_path)) is None


def test_npm_default_placeholder_script_is_not_a_signal(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}',
    )

    assert discover_validation_command("do something", str(tmp_path)) is None


def test_missing_project_root_returns_nothing() -> None:
    assert discover_validation_command("x", "") is None
    assert discover_validation_command("x", "/no/such/path/here") is None
