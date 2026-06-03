# AI Technical Lead Agent - Agent Instructions

## Startup protocol

Before searching or editing:
1. Read this file.
2. Load the single most relevant `.skills/<area>/SKILL.md`.
3. Inspect current code before giving code-specific advice.
4. Do not read the whole repo unless explicitly needed.

## Project goal

Build a local AI orchestration system for learning, portfolio value, and controlled coding-agent handoff.

Initial scope:
- Read one backlog task.
- Create a bounded execution brief.
- Pause for human approval when risky.
- Prepare a coding-agent instruction package.
- Run Codex only through the controlled coding-agent execution path.
- Later add other approved coding-agent providers behind the same runner boundary.

## Source hierarchy

- `AGENTS.md`: project-wide rules.
- `.skills/*/SKILL.md`: scoped rules loaded only for that work area.
- `docs/ARCHITECTURE.md`: durable module boundaries, adapter strategy, and test strategy.
- `docs/plan/INITIAL_PLAN.md`: durable project plan, setup notes, LangGraph notes, and current direction.
- `docs/BACKLOG.md`: current local prototype backlog.

## Skill routing

Do not read all skills. Choose the one best matching the task.

| Skill | Use when | File |
|---|---|---|
| `code-change` | Code, tests, runtime, graph, or integration changes. | `.skills/code-change/SKILL.md` |
| `backlog-management` | Backlog parsing, selection, status, evidence, or task handoff. | `.skills/backlog-management/SKILL.md` |
| `instruction-maintenance` | Editing AGENTS.md, skills, handoff docs, or project instructions. | `.skills/instruction-maintenance/SKILL.md` |

## Non-negotiables

- Never guess when current code or docs can be checked.
- Follow `docs/ARCHITECTURE.md` for module boundaries before adding new adapters, graph nodes, settings, or runner behavior.
- Keep work bounded and small.
- Prototype scope does not justify throwaway patterns. Use maintainable module boundaries, explicit validation, and code structure that can grow without a planned migration.
- Browser JavaScript must use ES modules. Load scripts with `type="module"` and prefer module imports/exports over global script patterns as frontend code grows.
- Do not create hidden autonomous behaviour.
- Do not hardcode hidden choices. If a default exists, name it as demo or fallback explicitly.
- Human approval is required for destructive, broad, risky, ambiguous, or expensive actions.
- Do not mask failures with broad fallback logic or silent defaults.
- Touch only files required for the task.
- Add concise help text/comments when behaviour is not obvious.
- Do not claim done without validation evidence.

## Testing rule

Use risk-based validation:
- For code changes, run the smallest relevant validation command.
- For graph changes, test the affected route.
- For startup/config changes, run the app startup command.
- Record the exact validation result before calling work done.

## Definition of Done

A change is done only when:
1. The changed behaviour was validated.
2. The validation command/result is reported.
3. No unrelated refactor was introduced.
4. Any new non-obvious behaviour has concise comments or help text.
5. Remaining risks or unfinished parts are stated clearly.
