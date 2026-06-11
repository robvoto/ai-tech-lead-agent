from __future__ import annotations

from ai_tech_lead.config import SETTINGS_PATH


def test_local_runtime_files_live_under_data() -> None:
    assert SETTINGS_PATH.parent.name == "data"
    assert SETTINGS_PATH.name == "coding_agent_settings.json"
