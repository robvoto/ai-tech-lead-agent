---
name: telegram-routing-review
description: Use when changing Telegram commands, natural-language routing, approval messages, or operator-facing status/help text.
---

# Skill: Telegram Routing Review

Use before editing Telegram operator behaviour.

## Rules

- Keep Telegram output phone-first: short, readable, and action-oriented.
- Do not expose raw graph state, raw prompts, full request bodies, secrets, tokens, or internal approval payloads.
- Keep `/status`, `/help`, `/approve`, and `/reject` explicit.
- Keep `/code` as the unambiguous coding-task command; `/fix` may remain only as an alias if current code supports it.
- Natural-language routing must not silently run the coding agent.
- Coding-agent execution must remain controlled by validated settings/admin and human approval.

## Review checklist

1. Inspect the Telegram adapter/operator file before editing.
2. Check whether the change affects intent routing, approval, message formatting, or settings.
3. Add/update targeted tests for command routing or compact message text.
4. Confirm logs preserve detail that Telegram intentionally hides.

## Validation

Preferred targeted validation:

```bash
uv run pytest tests/test_telegram_operator.py tests/test_telegram_intent_router.py -q
```
