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

## LangGraph interrupt pattern (Academy Lesson 4 — dynamic breakpoints)

Human-in-the-loop nodes use `interrupt()` inside the node, NOT `interrupt_before` at compile time.

```python
# Node calls interrupt() with a structured value
result = interrupt({"kind": "approval", "reason": reason, "formulated_task": task})
approved = result.get("approved", False)
return {"approved": approved, "approved_by": result.get("approved_by", "")}
```

```python
# Operator resumes with Command(resume=value)
app.invoke(Command(resume={"approved": True, "approved_by": sender}), config=thread_config)
```

Detect the interrupt from the state snapshot:
```python
interrupt_value = state_snapshot.tasks[0].interrupts[0].value  # dict with "kind"
```

## Node return pattern

All nodes return `dict[str, Any]` (partial state), not the full `GraphState`. Only return keys that changed.

```python
def my_node(state: GraphState) -> dict[str, Any]:
    ...
    return {"key": value}
```

## Annotated reducer for append-only lists

```python
from typing import Annotated
import operator

class GraphState(TypedDict):
    task_feedback: Annotated[list[str], operator.add]  # appends, never replaces
```

Return a list with the new item only — the reducer accumulates:
```python
return {"task_feedback": ["new feedback item"]}
```

## Bounded retry-then-fail-clearly interrupt pattern

Several human interrupt nodes (plan guidance, coding failure guidance, completion
verification, context clarification) share the same shape: pause, get a human answer,
loop back to retry — but only up to a fixed number of rounds.

- Track the attempt/retry count in graph state (e.g. `context_clarification_retry_count`).
- Compare it to a module-level constant (e.g. `CONTEXT_CLARIFICATION_MAX_RETRIES`) in the
  *router*, before deciding to interrupt again — not inside the interrupt node itself.
- When the limit is reached, route straight to a terminal outcome instead of interrupting
  again. Set an explicit boolean for that terminal state (e.g. `context_clarification_exhausted`)
  rather than overloading a shared/generic flag — the subprocess boundary (`agent_task_runner.py`)
  needs an unambiguous signal to report a clear failure instead of inviting another retry.

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
