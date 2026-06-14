from __future__ import annotations

from typing import Any

from ai_tech_lead.config import PROJECT_ROOT


def valid_settings_dict() -> dict[str, Any]:
    return {
        "project_root": str(PROJECT_ROOT),
        "backlog_path": "data/backlog/ai_tech_lead_backlog.xlsx",
        "max_runtime_minutes": 20,
        "coding_agent_command": "codex",
        "coding_agent_args": ["--ask-for-approval", "never", "exec"],
        "execute_coding_agent": False,
        "telegram_enabled": True,
        "admin_bind_host": "127.0.0.1",
        "admin_bind_port": 8766,
        "orchestrator_ai_enabled": False,
        "orchestrator_ai_model": "gpt-4.1-mini",
        "orchestrator_ai_max_output_tokens": 300,
        "orchestrator_ai_timeout_seconds": 20,
        "coding_agent_progress_interval_seconds": 1,
        "allowed_directories": ["src/ai_tech_lead", "docs", "tests"],
        "watched_directories": ["src/ai_tech_lead", "docs"],
        "brief_constraints": ["Keep the change small"],
        "acceptance_criteria": [
            "Code runs successfully",
            "Changed files are listed",
            "Validation command and result are reported",
        ],
        "risk_notes": ["Approval is controlled by the selected backlog item."],
        "telegram_transport": "polling",
        "telegram_api_base_url": "https://api.telegram.org",
        "telegram_long_poll_timeout_seconds": 25,
        "telegram_max_code_request_chars": 3000,
        "telegram_max_message_chars": 3900,
        "telegram_allowed_chat_ids": [],
        "telegram_webhook_url": "",
        "telegram_webhook_bind_host": "127.0.0.1",
        "telegram_webhook_bind_port": 8080,
        "telegram_webhook_secret_token": "",
    }
