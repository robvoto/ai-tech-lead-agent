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
- Prefer clear classes/functions with one responsibility over large files with mixed concerns.
- Do not refactor unrelated modules.
- Do not introduce compatibility shims, duplicate implementations, legacy paths, unused variables, unused functions, or dead paths unless the human explicitly agrees.
- Remove old code when the project is not in production and no explicit backwards-compatibility requirement exists.
- Before adding any constant, command, threshold, URL, timeout, limit, feature toggle, rule, or product behaviour, classify it as protocol-owned, product-owned, or user/admin-owned. Protocol-owned values must live close to the adapter boundary with a short reason. Product-owned vocabulary, such as Telegram commands, must live in a small explicit registry/config module. User/admin-owned runtime behaviour must live in validated settings/admin JSON. Do not scatter these values through implementation code.
- Do not hide failures with broad exception swallowing, guessed fields, fallback labels, default models, or silent alternate paths.
- Surface missing or invalid values clearly.
- Add concise comments, docstrings, CLI help, or UI help text when intent, ownership, graph flow, or integration boundaries are not obvious to a human learner.
- Explain important implementation reasons in the finish report, not inside this skill file, when the choice teaches something about LangGraph, LangChain, agent orchestration, subprocess safety, or token/cost control.
- Telegram output is phone-first. Keep operator messages short, human-readable, and action-oriented. Do not send raw graph state, raw internal prompts, duplicated request bodies, or long diagnostic text to Telegram.
- Stop at a useful checkpoint and ask before continuing if the task becomes broad, uncertain, token-heavy, or likely to create architecture debt, unless the human explicitly requested unattended automation.
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
- Why this design
- Validation
- Remaining
