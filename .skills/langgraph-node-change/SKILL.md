---
name: langgraph-node-change
description: Use when changing LangGraph nodes, routers, graph state, checkpoints, interrupts, or persistence behaviour.
---

# Skill: LangGraph Node Change

Use before editing graph workflow behaviour.

## Rules

- A graph node should do one clear thing.
- Keep graph construction separate from terminal demo runners.
- Name conditional routing functions as routers.
- Use explicit path maps for conditional routes.
- Store raw state, not preformatted prompts.
- Assemble prompts close to the node that uses them.
- Use structured LLM outputs for routing, classification, and risk-review decisions.
- Use interrupts/checkpoints for human approval paths.
- Put external actions in separate nodes with clear retry and error behaviour.
- Keep downstream handoff context bounded.

## Review checklist

1. Inspect `docs/ARCHITECTURE.md` and the affected graph/node files before editing.
2. Identify whether the change affects state shape, routing, persistence, approval resume, or external execution.
3. Add or update targeted tests for the affected route.
4. Keep diagrams and log labels understandable when graph behaviour changes.

## Validation

Run the smallest test set that proves the affected route. For approval/routing changes, start with:

```bash
uv run pytest tests/test_risk_reviewer.py tests/test_instruction_assembler.py -q
```
