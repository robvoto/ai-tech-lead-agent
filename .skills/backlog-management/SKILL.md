---
name: backlog-management
description: Use for backlog loading, parsing, selection, status, evidence, and task handoff rules.
---

# Skill: Backlog Management

Use when creating, parsing, selecting, updating, or explaining backlog items.

## Rules
- Do not let the agent silently choose work in a hidden way.
- Normal execution should select a backlog item by explicit ID.
- Demo-only selection may exist, but the function name must make that obvious, for example `load_first_backlog_item_for_demo`.
- Backlog parsing should expose clear errors for missing files, missing headings, duplicate IDs, or unknown requested IDs.
- Do not delete backlog items without human approval.
- Do not mark work done without validation evidence.
- Preserve the human’s original intent when formatting or converting backlog items.

## Current local prototype convention
- Local backlog path: `docs/BACKLOG.md`.
- Current markdown item format:

```markdown
## JH-001 - Add Telegram input placeholder

Goal:
...

Constraints:
- ...
```

## Future selection direction
Move from hardcoded ID selection to one of these explicit mechanisms:
- CLI argument, for example `--task-id JH-001`.
- Telegram command, for example `/run JH-001`.
- Human approval list, where the agent shows candidate tasks and waits for one choice.

## Finish format
Report:
- Backlog item selected or changed
- Selection rule used
- Validation evidence
