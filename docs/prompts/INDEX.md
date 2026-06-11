# Prompt Files Index

Use this folder for prompt and rule text loaded by graph nodes, instruction builders, or message builders.

## Files

- `backlog_ownership_rules.md` - backlog ownership rules for coding-agent handoffs.
- `clarification_check_prompt.md` - prompt for deciding whether a request needs clarification.
- `completion_summary_prompt.md` - prompt rules for concise completion summaries.
- `plan_request_instruction.md` - prompt text for requesting an implementation plan.
- `plan_review_prompt.md` - prompt for reviewing a proposed implementation plan.
- `risk_review_prompt.md` - prompt for risk-review classification and approval reasoning.
- `task_formulation_prompt.md` - prompt for turning human input into a focused coding task.
- `telegram_agent_system.md` - system prompt for Telegram-facing assistant behaviour.

## Maintenance rules

- Keep runtime prompt text here, not in `AGENTS.md`.
- Keep human explanations in `docs/*` when they are not loaded by runtime code.
- Keep each prompt file single-purpose.
- Remove or merge prompt files once code no longer references them.
