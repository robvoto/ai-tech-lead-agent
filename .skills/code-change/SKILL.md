---
name: code-change
description: Use for code, tests, runtime, graph, or integration implementation changes.
---

# Skill: Code Change

Use before modifying existing code.

## Rules
- Inspect the target file before editing.
- Check `docs/ARCHITECTURE.md` before changing adapters, graph flow, settings, subprocess execution, or UI boundaries.
- Touch only files required for the task.
- Keep changes small and scoped.
- Prefer small, single-purpose modules over monoliths.
- Do not refactor unrelated modules.
- Do not introduce compatibility shims, duplicate implementations, or dead paths unless the human explicitly agrees.
- Do not hide failures with broad exception swallowing, guessed fields, fallback labels, default models, or silent alternate paths.
- Surface missing or invalid values clearly.
- Add concise comments or docstrings when intent, ownership, graph flow, or integration boundaries are not obvious.
- Remove temporary helper scripts before finishing unless the human explicitly wants to keep them.

## Graph-specific rules
- Keep graph construction separate from terminal demo runners.
- A graph node should have one clear responsibility.
- Conditional routing functions should be named as routers, not normal action nodes.
- Use explicit path maps for conditional routes so diagrams stay understandable.
- Do not add hidden autonomous behaviour. If a step chooses work, the selection rule must be explicit and visible.

## Testing
Use risk-based validation:
- Use `uv run pytest` as the default core validation command when the change touches tested Python behavior.
- Run the smallest command that proves the changed behaviour.
- Add adjacent validation when a change crosses graph routing, persistence, CLI startup, logging, or config boundaries.
- Record the exact validation command before calling work done.

## Finish format
Report:
- Changed
- Validation
- Remaining
