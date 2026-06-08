export const LIST_FIELDS = [
  "coding_agent_args",
  "allowed_directories",
  "watched_directories",
  "brief_constraints",
  "acceptance_criteria",
  "risk_notes",
  "telegram_allowed_chat_ids",
];

export const PROMPT_FIELDS = [
  "risk_review_reason_template",
  "execution_brief_template",
];

export const LIST_FIELD_CONFIG = {
  coding_agent_args: {
    placeholder: "--ask-for-approval",
  },
  allowed_directories: {
    placeholder: "src/ai_tech_lead",
  },
  watched_directories: {
    placeholder: "docs",
  },
  brief_constraints: {
    placeholder: "Keep the change small",
  },
  acceptance_criteria: {
    placeholder: "Changed files are listed",
  },
  risk_notes: {
    placeholder: "This is not keyword risk detection.",
  },
  telegram_allowed_chat_ids: {
    placeholder: "8754838132",
  },
};

export const FIELD_PLACEHOLDERS = {
  backlog_path: "docs/BACKLOG.md",
  max_runtime_minutes: "20",
  coding_agent_command: "codex",
  admin_bind_host: "127.0.0.1",
  admin_bind_port: "8766",
  orchestrator_ai_model: "gpt-4.1-mini",
  orchestrator_ai_max_output_tokens: "300",
  orchestrator_ai_timeout_seconds: "20",
  telegram_api_base_url: "https://api.telegram.org",
  telegram_long_poll_timeout_seconds: "25",
  telegram_max_fix_request_chars: "3000",
  telegram_max_message_chars: "3900",
  telegram_bot_token: "1234567890:AA...",
  telegram_webhook_url: "https://example.com/telegram/webhook",
  telegram_webhook_bind_host: "127.0.0.1",
  telegram_webhook_bind_port: "8080",
  telegram_webhook_secret_token: "optional secret",
};
