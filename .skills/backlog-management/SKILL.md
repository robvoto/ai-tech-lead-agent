---
name: backlog-management
description: Use for backlog loading, parsing, selection, status, evidence, and task handoff rules.
---

# Skill: Backlog Management

Use when creating, parsing, selecting, updating, or explaining backlog items.

## Rules
- Do not let the agent silently choose work in a hidden way.
- Normal execution should select a backlog item by explicit ID.
- Demo-only selection may exist only when clearly named as demo behavior, for example `load_first_backlog_item_for_demo`.
- Backlog parsing should expose clear errors for missing files, missing headings, duplicate IDs, or unknown requested IDs.
- Each executable backlog item must include explicit `Approval Required` and `Approval Reason` fields.
- Do not delete backlog items without human approval.
- Do not mark work done without validation evidence.
- Preserve the human’s original intent when formatting or converting backlog items.

## Current local prototype convention
- Local backlog path: `docs/BACKLOG.md`.
- Normal CLI selection uses `--task-id`, for example `uv run python -m ai_tech_lead --task-id JH-001`.
- Current markdown item format:

```markdown
## JH-001 - Add Telegram input placeholder

Approval Required: no
Approval Reason: Safe placeholder task. It does not connect the real Telegram API or use secrets.

Goal:
...

Constraints:
- ...
```

## Future selection direction
Additional input adapters may select tasks through explicit mechanisms:
- Telegram command, for example `/run JH-001`.
- Web form or human approval list, where the agent shows candidate tasks and waits for one choice.

## Finish format
Report:
- Backlog item selected or changed
- Selection rule used
- Validation evidence
