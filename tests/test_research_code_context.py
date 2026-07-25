from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.research_code_context import (
    collect_code_context,
    format_code_context_for_prompt,
)


def _settings(tmp_path: Path, **overrides):
    fields = {
        "project_root": str(tmp_path),
        "watched_directories": ["src"],
        "research_max_code_context_files": 5,
        "research_max_excerpt_chars": 200,
    }
    fields.update(overrides)
    return replace(parse_settings(valid_settings_dict()), **fields)


def test_collect_code_context_selects_matching_file_by_path(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "telegram_client.py").write_text(
        "def send_with_retry():\n    pass\n", encoding="utf-8"
    )
    (src_dir / "unrelated_module.py").write_text("def unrelated():\n    pass\n", encoding="utf-8")

    settings = _settings(tmp_path)
    sources = collect_code_context("Add retry handling to the telegram client", settings)

    assert [s.path for s in sources] == ["src/telegram_client.py"]
    assert "send_with_retry" in sources[0].excerpt


def test_collect_code_context_disabled_returns_empty(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "telegram_client.py").write_text("send_with_retry", encoding="utf-8")

    settings = _settings(tmp_path, research_code_context_enabled=False)

    assert collect_code_context("telegram client retry", settings) == []


def test_collect_code_context_returns_nothing_when_no_file_matches(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "unrelated_module.py").write_text("def unrelated():\n    pass\n", encoding="utf-8")

    settings = _settings(tmp_path)

    assert collect_code_context("Add retry handling to the telegram client", settings) == []


def test_collect_code_context_ignores_noise_directories(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    venv_dir = src_dir / ".venv" / "telegram_client"
    venv_dir.mkdir(parents=True)
    (venv_dir / "telegram_client.py").write_text("send_with_retry", encoding="utf-8")
    pycache_dir = src_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "telegram_client.pyc").write_text("send_with_retry", encoding="utf-8")

    settings = _settings(tmp_path)

    assert collect_code_context("telegram client retry", settings) == []


def test_collect_code_context_respects_max_files_cap(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for i in range(5):
        (src_dir / f"telegram_client_{i}.py").write_text("send_with_retry", encoding="utf-8")

    settings = _settings(tmp_path, research_max_code_context_files=2)
    sources = collect_code_context("telegram client retry", settings)

    assert len(sources) == 2


def test_collect_code_context_missing_watched_directory_is_skipped(tmp_path: Path) -> None:
    settings = _settings(tmp_path, watched_directories=["does-not-exist"])

    assert collect_code_context("telegram client retry", settings) == []


def test_collect_code_context_scans_project_root_override_not_settings_root(
    tmp_path: Path,
) -> None:
    """A resolved target project's code must be scanned, not this repo's own."""

    own_repo_root = tmp_path / "ai-tech-lead"
    own_repo_root.mkdir()
    (own_repo_root / "src").mkdir()
    (own_repo_root / "src" / "telegram_client.py").write_text(
        "send_with_retry  # belongs to ai-tech-lead itself", encoding="utf-8"
    )

    target_project_root = tmp_path / "some-other-project"
    (target_project_root / "src").mkdir(parents=True)
    (target_project_root / "src" / "telegram_client.py").write_text(
        "send_with_retry  # belongs to the target project", encoding="utf-8"
    )

    settings = _settings(tmp_path, project_root=str(own_repo_root))
    sources = collect_code_context(
        "Add retry handling to the telegram client",
        settings,
        project_root_override=str(target_project_root),
    )

    assert len(sources) == 1
    assert sources[0].path == "src/telegram_client.py"
    assert "belongs to the target project" in sources[0].excerpt
    assert "belongs to ai-tech-lead itself" not in sources[0].excerpt


def test_collect_code_context_defaults_to_settings_root_when_no_override(
    tmp_path: Path,
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "telegram_client.py").write_text("send_with_retry", encoding="utf-8")

    settings = _settings(tmp_path)
    sources = collect_code_context(
        "telegram client retry", settings, project_root_override=None
    )

    assert len(sources) == 1


def test_format_code_context_for_prompt_empty_sources() -> None:
    assert format_code_context_for_prompt([]) == "(none)"


def test_format_code_context_for_prompt_renders_path_and_excerpt(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "telegram_client.py").write_text("send_with_retry", encoding="utf-8")

    settings = _settings(tmp_path)
    sources = collect_code_context("telegram client retry", settings)
    rendered = format_code_context_for_prompt(sources)

    assert "src/telegram_client.py" in rendered
    assert "send_with_retry" in rendered
