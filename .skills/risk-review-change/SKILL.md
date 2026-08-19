---
name: risk-review-change
description: Use when changing the LLM risk reviewer, approval model, safety gates, or structured risk-review outputs.
---

# Skill: Risk Review Change

Use before editing approval/risk-review behaviour.

## Rules

- Human approval stays mandatory before real coding-agent execution.
- Do not replace enforced settings/admin/code gates with prompt-only instructions.
- Risk decisions must use structured fields, not free-text-only output.
- Do not use keyword-only heuristics as the final approval decision.
- Keep risky, destructive, ambiguous, broad, expensive, or code-executing actions gated.
- Store detailed reasoning in state/logs; send compact summaries to Telegram.

## Review checklist

1. Inspect the risk reviewer and approval flow before editing.
2. Confirm whether the change affects graph routing, settings, persistence, or Telegram messages.
3. Preserve explicit failure behaviour for invalid or missing structured LLM output.
4. Add/update tests for approval required, approval not required, malformed output, and compact operator messaging when relevant.

## Validation

Preferred targeted validation:

```bash
uv run pytest tests/test_risk_reviewer.py tests/test_instruction_assembler.py -q
```
