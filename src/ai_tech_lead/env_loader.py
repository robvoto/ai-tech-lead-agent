"""Load local environment variables without exposing secret values."""

from __future__ import annotations

import os
from pathlib import Path

from ai_tech_lead.config import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"


def load_local_env(env_path: Path = ENV_PATH) -> None:
    """Load simple KEY=VALUE pairs from .env without overriding real env vars."""

    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
