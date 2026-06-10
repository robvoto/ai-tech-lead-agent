"""Load repository-visible prompt and rule text from docs/prompts."""

from __future__ import annotations

from pathlib import Path

from ai_tech_lead.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "docs" / "prompts"


def load_prompt(filename: str) -> str:
    """Load one required prompt file from docs/prompts.

    Prompt files are intentionally checked into the repository so humans can
    review and improve them without hunting through Python strings.
    """

    normalized_filename = filename.strip()
    if not normalized_filename:
        raise ValueError("Prompt file name cannot be empty.")

    prompt_path = PROMPTS_DIR / Path(normalized_filename)
    if not prompt_path.is_file():
        raise FileNotFoundError(
            f"Required prompt file not found: {prompt_path}. "
            "Expected repository-visible prompt text under docs/prompts."
        )

    return prompt_path.read_text(encoding="utf-8").strip()
