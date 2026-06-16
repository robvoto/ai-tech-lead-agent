export const LIST_FIELDS = [
  "coding_agent_args",
  "project_context",
  "allowed_directories",
  "watched_directories",
  "brief_constraints",
  "acceptance_criteria",
  "risk_notes",
  "research_local_index_paths",
  "research_allowed_domains",
  "research_online_source_urls",
  "telegram_allowed_chat_ids",
];

export const PROMPTS_API_ROUTE = "/api/prompts";

export const PROMPT_CLASS_OPTIONS = [
  { value: "instruction", label: "Instruction" },
  { value: "decision", label: "Decision" },
  { value: "guidance", label: "Guidance" },
  { value: "system", label: "System" },
  { value: "template", label: "Template" },
  { value: "policy", label: "Policy" },
];

export const PROMPT_TEMPLATE_PLACEHOLDER =
  "Write the prompt text here. Use placeholders like {request}, {brief}, or {task_feedback}.";

export const LIST_FIELD_CONFIG = {
  coding_agent_args: {
    placeholder: "--ask-for-approval",
  },
  project_context: {
    placeholder: "Architecture and module map: docs/ARCHITECTURE.md",
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
  research_local_index_paths: {
    placeholder: "docs/INDEX.md",
  },
  research_allowed_domains: {
    placeholder: "docs.langchain.com",
  },
  research_online_source_urls: {
    placeholder: "https://docs.langchain.com/oss/python/langgraph/overview",
  },
  telegram_allowed_chat_ids: {
    placeholder: "8754838132",
  },
};

export const FIELD_PLACEHOLDERS = {
  project_root: "/mnt/e/Programming/ai-tech-lead",
  backlog_path: "data/backlog.sqlite3",
  max_runtime_minutes: "20",
  coding_agent_command: "codex",
  admin_bind_host: "127.0.0.1",
  admin_bind_port: "8766",
  orchestrator_ai_model: "gpt-4.1-mini",
  orchestrator_ai_max_output_tokens: "300",
  orchestrator_ai_timeout_seconds: "20",
  research_min_local_sources: "2",
  research_max_local_sources: "4",
  research_max_online_source_urls: "4",
  research_fetch_timeout_seconds: "10",
  research_max_excerpt_chars: "800",
  telegram_api_base_url: "https://api.telegram.org",
  telegram_long_poll_timeout_seconds: "25",
  telegram_max_code_request_chars: "3000",
  telegram_max_message_chars: "3900",
  telegram_webhook_url: "https://example.com/telegram/webhook",
  telegram_webhook_bind_host: "127.0.0.1",
  telegram_webhook_bind_port: "8080",
  telegram_webhook_secret_token: "optional secret",
};
