from __future__ import annotations

from pathlib import Path

import pytest

from ai_tech_lead.prompt_loader import PROMPTS_DIR, load_prompt


@pytest.mark.parametrize(
    "filename",
    [
        "telegram_agent_system.md",
        "risk_review_prompt.md",
        "coding_agent_handoff_rules.md",
        "backlog_ownership_rules.md",
        "completion_summary_prompt.md",
    ],
)
def test_required_prompt_files_exist(filename: str) -> None:
    prompt_path = PROMPTS_DIR / filename

    assert prompt_path.is_file()
    assert prompt_path.read_text(encoding="utf-8").strip()


def test_prompt_loader_reads_prompt_file_correctly(monkeypatch, tmp_path: Path) -> None:
    prompts_dir = tmp_path / "docs" / "prompts"
    prompts_dir.mkdir(parents=True)
    prompt_path = prompts_dir / "demo_prompt.md"
    prompt_path.write_text("Hello prompt\n", encoding="utf-8")
    monkeypatch.setattr("ai_tech_lead.prompt_loader.PROMPTS_DIR", prompts_dir)

    assert load_prompt("demo_prompt.md") == "Hello prompt"


def test_missing_prompt_file_raises_clear_error(monkeypatch, tmp_path: Path) -> None:
    prompts_dir = tmp_path / "docs" / "prompts"
    prompts_dir.mkdir(parents=True)
    monkeypatch.setattr("ai_tech_lead.prompt_loader.PROMPTS_DIR", prompts_dir)

    with pytest.raises(FileNotFoundError, match="Required prompt file not found"):
        load_prompt("missing_prompt.md")
