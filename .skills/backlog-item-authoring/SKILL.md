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

## Validation

Preferred targeted validation:

```bash
uv run pytest tests/test_backlog_repository.py tests/test_backlog_draft_builder.py -q
```
