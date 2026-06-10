# AI Technical Lead Agent - Agent Instructions

## Startup protocol

Before searching or editing:
1. Read this file.
2. Treat this as a WSL-first project. Runtime commands must run from WSL at `/mnt/e/Programming/ai-tech-lead`, not from PowerShell at `E:\Programming\ai-tech-lead`.
3. Load the single most relevant `.skills/<area>/SKILL.md`.
4. Inspect current code before giving code-specific advice.
5. Do not read the whole repo unless explicitly needed.

## Project purpose

Build a safe local AI orchestration system that receives work, classifies intent, reviews risk, asks for approval when needed, and hands off bounded coding tasks.

The project should demonstrate disciplined LangGraph/LangChain engineering, not uncontrolled autonomous coding.

## Source hierarchy

- `AGENTS.md`: project-wide operational rules for coding agents.
- `CLAUDE.md`: Claude Code compatibility shim that imports this file.
- `.skills/*/SKILL.md`: scoped operational rules loaded only for that work area.
- `docs/ARCHITECTURE.md`: durable module boundaries, adapter strategy, test strategy, and developer-facing architecture explanations.
- `docs/CONTEXT_MANAGEMENT.md`: context boundaries for graph state, prompts, logs, Telegram, and coding-agent handoffs.
- `docs/plan/INITIAL_PLAN.md`: temporary project direction and roadmap. Read when a task touches planning or setup, but do not treat it as policy.
- `docs/BACKLOG.md`: current local prototype backlog.

## Skill routing

Do not read all skills. Choose the one best matching the task.

| Skill | Use when | File |
|---|---|---|
| `code-change` | Code, tests, runtime, graph, or integration changes. | `.skills/code-change/SKILL.md` |
| `backlog-management` | Backlog parsing, selection, status, evidence, or task handoff. | `.skills/backlog-management/SKILL.md` |
| `instruction-maintenance` | Editing AGENTS.md, skills, handoff docs, or project instructions. | `.skills/instruction-maintenance/SKILL.md` |
| `telegram-routing-review` | Telegram command/intent routing or operator message changes. | `.skills/telegram-routing-review/SKILL.md` |
| `risk-review-change` | LLM risk reviewer, approval, or safety-gate changes. | `.skills/risk-review-change/SKILL.md` |
| `backlog-item-authoring` | Drafting or validating new backlog items. | `.skills/backlog-item-authoring/SKILL.md` |
| `langgraph-node-change` | LangGraph node, router, checkpoint, interrupt, or graph-state changes. | `.skills/langgraph-node-change/SKILL.md` |

## Non-negotiables

- Never guess when current code or docs can be checked.
- Follow `docs/ARCHITECTURE.md` before adding adapters, graph nodes, settings, runners, storage, or UI boundaries.
- Keep work bounded and small.
- Touch only files required for the task.
- Do not add legacy compatibility paths, compatibility shims, dead code, duplicated implementations, unused code, or unused variables unless the human explicitly asks for them.
- Do not create hidden autonomous behaviour.
- Do not hardcode hidden choices. If hardcoding is intentionally used for a prototype or safety boundary, explain what is hardcoded, why it is acceptable now, and what would make it configurable later.
- Human approval is required for destructive, broad, risky, ambiguous, expensive, or code-executing actions.
- Runtime safety must be enforced in settings/admin/code, not only in instruction files.
- Do not mask failures with broad fallback logic or silent defaults.
- If unsure, stop and ask instead of inventing, guessing, or silently choosing an architecture.
- Do not claim done without validation evidence provided by the CLI result.

## Architecture rules

- Keep separation of concerns clear: adapters receive input, graph nodes orchestrate workflow state, settings own configuration, runners execute subprocesses, and storage persists data.
- Prefer small, focused modules and classes over large files with mixed responsibilities.
- Browser code must keep HTML, CSS, and JavaScript separated.
- Browser JavaScript must use ES modules. Load scripts with `type="module"` and prefer imports/exports over global script patterns as frontend code grows.
- Do not add permanent global frontend variables or hidden UI configuration. Prefer JSON/API-provided configuration or small explicit module constants with a comment explaining why they are local-only.

## LangGraph workflow rules

- A graph node should do one clear thing.
- Conditional routing functions should be named as routers, not normal action nodes.
- Use explicit path maps for conditional routes so diagrams stay understandable.
- Use structured LLM outputs for classification, routing, and risk-review decisions.
- Use interrupts/checkpoints for human approval paths.
- External actions belong in separate nodes with clear retry/error behaviour.
- Store raw state, not preformatted prompts.
- Assemble prompts close to the node that uses them.
- If a workflow step makes a decision, the rule or reason must be visible in code, logs, graph state, or the finish report.

## Context management

- Keep agent-visible context bounded.
- Do not inject unbounded logs, full backlog dumps, raw graph state, or long prompt bodies into handoffs.
- Do not expose raw graph state, raw prompts, secrets, tokens, or internal approval payloads to Telegram.
- Store raw state and format prompts on demand inside the node that needs them.
- Any new context fragment over roughly 1,000 tokens needs explicit justification in code comments, docs, or the finish report.
- Detailed context rules belong in `docs/CONTEXT_MANAGEMENT.md`.

## Telegram/operator rules

- Telegram is phone-first.
- Operator messages must be short, human-readable, action-oriented, and must not expose raw graph state, internal prompt text, or duplicated request bodies.
- Keep approval messages compact; preserve detailed reasons in graph state/logs.

## Validation commands

Use risk-based validation. Run the smallest relevant command that proves the changed behaviour.

Common targeted commands:
- `uv run pytest tests/test_telegram_operator.py tests/test_telegram_intent_router.py -q`
- `uv run pytest tests/test_risk_reviewer.py tests/test_instruction_assembler.py -q`
- `uv run pytest tests/test_backlog_repository.py tests/test_backlog_draft_builder.py -q`
- `uv run python -m py_compile <touched python files>`

## Definition of Done

A change is done only when:
1. The changed behaviour was validated, or the reason validation was not applicable is stated.
2. The validation command/result is reported.
3. No unrelated refactor was introduced.
4. New non-obvious behaviour has concise comments or help text.
5. Remaining risks or unfinished parts are stated clearly.
6. No new unused code, duplicate implementation, or unexplained hardcoding remains.
7. Teaching notes are kept in the finish report or `docs/*`, not bloated into agent-facing rules.
