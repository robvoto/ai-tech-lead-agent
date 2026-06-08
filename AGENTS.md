# AI Technical Lead Agent - Agent Instructions

## Startup protocol

Before searching or editing:
1. Read this file.
2. Treat this as a WSL-first project. Runtime commands must run from WSL at `/mnt/e/Programming/ai-tech-lead`, not from PowerShell at `E:\Programming\ai-tech-lead`.
3. Load the single most relevant `.skills/<area>/SKILL.md`.
4. Inspect current code before giving code-specific advice.
5. Do not read the whole repo unless explicitly needed.

## Source hierarchy

- `AGENTS.md`: project-wide operational rules for coding agents.
- `.skills/*/SKILL.md`: scoped operational rules loaded only for that work area.
- `docs/ARCHITECTURE.md`: durable module boundaries, adapter strategy, test strategy, and developer-facing architecture explanations.
- `docs/plan/INITIAL_PLAN.md`: temporary project direction, setup notes, LangGraph notes, and current roadmap. Read it when a task touches planning or setup, but do not treat it as policy.
- `docs/BACKLOG.md`: current local prototype backlog.

## Skill routing

Do not read all skills. Choose the one best matching the task.

| Skill | Use when | File |
|---|---|---|
| `code-change` | Code, tests, runtime, graph, or integration changes. | `.skills/code-change/SKILL.md` |
| `backlog-management` | Backlog parsing, selection, status, evidence, or task handoff. | `.skills/backlog-management/SKILL.md` |
| `instruction-maintenance` | Editing AGENTS.md, skills, handoff docs, or project instructions. | `.skills/instruction-maintenance/SKILL.md` |

## Operating principles

This project exists to build a professional, teachable, local AI orchestration system.
It should demonstrate disciplined LangGraph/LangChain engineering, not repeat OpenClaw-style uncontrolled behaviour.

Prefer taking longer to produce clean, understandable code over shipping rushed code that creates future cleanup work.

## Documentation audience

- Write `AGENTS.md` and `.skills/*/SKILL.md` for coding agents: concise, operational, specific, and easy to follow.
- Write `docs/*` for humans and developers: explanations, rationale, diagrams, trade-offs, and learning notes.
- Do not put long teaching material in agent-facing instruction files; link or summarize it in human/developer docs instead.

## Non-negotiables

- Never guess when current code or docs can be checked.
- Follow `docs/ARCHITECTURE.md` for module boundaries before adding new adapters, graph nodes, settings, or runner behavior.
- Keep work bounded and small.
- Prototype scope does not justify throwaway patterns. Use maintainable module boundaries, explicit validation, and code structure that can grow without a planned migration.
- Do not add legacy compatibility paths, compatibility shims, dead code, duplicated implementations, unused code, or unused variables unless the human explicitly asks for them.
- Keep separation of concerns clear: adapters receive input, graph nodes orchestrate workflow state, settings own configuration, runners execute subprocesses, and storage persists data.
- Prefer small, focused modules and classes over large files with mixed responsibilities.
- Browser code must keep HTML, CSS, and JavaScript separated. HTML owns structure, CSS owns presentation, and JavaScript owns behaviour.
- Browser JavaScript must use ES modules. Load scripts with `type="module"` and prefer module imports/exports over global script patterns as frontend code grows.
- Do not add permanent global frontend variables or hidden UI configuration. Prefer JSON/API-provided configuration or small explicit module constants with a comment explaining why they are local-only.
- Do not create hidden autonomous behaviour.
- Do not hardcode hidden choices. If hardcoding is intentionally used for a prototype or safety boundary, explain what is hardcoded, why it is acceptable now, and what would make it configurable later.
- Do not make the human remember CLI flag combinations for normal product behaviour. Runtime features such as Telegram enable/disable and coding-agent execution enable/disable belong in settings/admin. CLI flags are for development, diagnostics, or explicit debug paths only.
- Human approval is required for destructive, broad, risky, ambiguous, or expensive actions.
- If unsure, stop and ask instead of inventing, guessing, or silently choosing an architecture.
- Do not mask failures with broad fallback logic or silent defaults.
- Touch only files required for the task.
- Add concise, human-friendly help text/comments when behaviour, commands, graph flow, or integration boundaries are not obvious; keep those explanations short in agent-facing files.
- Telegram is phone-first. Operator messages must be short, human-readable, action-oriented, and must not expose raw graph state, internal prompt text, or duplicated request bodies.
- Teach the human in the finish report or `docs/*` when a design decision is relevant to learning AI agents, LangChain, LangGraph, cost control, or workflow orchestration.
- Maximize value per token. If a task becomes long, uncertain, or expensive, stop at a useful checkpoint and ask for direction unless the task explicitly requires unattended automation.
- Prefer deterministic, inspectable behaviour over clever magic. If a workflow step makes a decision, the rule or reason must be visible in code, logs, graph state, or the finish report.
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
6. No new unused code, duplicate implementation, or unexplained hardcoding remains.
7. Any teaching notes are kept in the finish report or `docs/*`, not bloated into agent-facing rules.
