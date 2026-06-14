---
name: backlog-item-authoring
description: Use when drafting, validating, or appending backlog items.
---

# Skill: Backlog Item Authoring

Use when creating or changing backlog items.

## Rules

- Do not silently choose work for the human.
- Require explicit human confirmation before adding or changing backlog items through the product flow.
- Every item must include ID, title, approval required, approval reason, goal, and constraints.
- Keep backlog items executable by explicit ID.
- Do not turn backlog items into universal operating rules.
- Put durable project rules in `AGENTS.md`; put repeatable procedures in `.skills`; put rationale in `docs`.
- For refined backlog items created through `/propose`, the AI must provide structured fields for title, item type, epic, priority, size, approval status, approval reason, problem, desired outcome, scope, out of scope, acceptance criteria, duplicate/stale/already-done checks, research use, implementation pattern, and risk flags.
- Workbook-managed columns such as `Status`, `Created Date`, `Creator`, `Has Goal`, `Has Problem`, `Has Outcome`, `Has Acceptance Criteria`, `Has Constraints`, `Missing Fields`, `Missing Count`, `Completeness`, `Cleanup Needed`, and `Notes / Cleanup Action` are filled by code, not by the AI.

## Draft format

Use this shape unless existing backlog conventions require otherwise:

```md
## ATL-XXX - Title

Approval Required: yes/no
Approval Reason: Why approval is or is not needed.

Goal:
One clear outcome.

Constraints:
- Constraint 1.
- Constraint 2.
```

For refined backlog items, keep the AI output structured and specific enough for the workbook import path to validate and populate the row safely. The orchestrator code adds the workbook-only columns after validation.

## Validation

Preferred targeted validation:

```bash
uv run pytest tests/test_backlog_repository.py tests/test_backlog_draft_builder.py -q
```
