# Documentation Index

Use this file as the single documentation entry point. Start here, then open only the document needed for the current task.

## Canonical project standards

- External standards source of truth: https://docs.google.com/document/d/1VDlwjmfUwGA-eveXeHD7rBJ4tRRBafloQwj62KWU3Q0/edit?tab=t.0
- If the standards document cannot be accessed, stop and ask the operator for the current exported text. Do not guess standards updates.

## Backlog source of truth

Human planning backlog for AI agents / AI Tech Lead data, setup, production, and knowledge-store work:

```text
https://docs.google.com/spreadsheets/d/1-e2lQ6vLUD8A5t3cuLhrjRvdTbs3hfs4ptdEE2yDaEc/edit
```

Runtime backlog source:

```text
data/backlog.sqlite3
```

Rules:

- The Google Sheet is the human planning backlog and the source of truth for backlog decisions.
- The running app reads runtime backlog items from SQLite.
- Do not add hidden alternate backlog sources or silent source switching.
- If a backlog source is missing, stale, inaccessible, or inconsistent, stop and ask the operator.

## Core project documents

- `ARCHITECTURE.md` - system architecture, module boundaries, adapter strategy, persistence, and growth rules.
- `GRAPH_WORKFLOW.md` - implemented LangGraph workflow, nodes, routes, interrupts, and execution boundary.
- `RUNTIME_RUNBOOK.md` - local run, test, Studio, admin, and troubleshooting commands.
- `diagrams/` - generated PNG and hash artifact for the main coding workflow.
- `ARCHITECTURE.md` also contains the JSON subprocess contract, input/output shape, and Telegram boundary.
- `CONTEXT_MANAGEMENT.md` - context allowed in graph state, prompts, Telegram messages, logs, and coding-agent handoffs.
- `BACKLOG.md` - archived Markdown backlog retained only for history and comparison at `data/backlog/archive/BACKLOG.md`.
- `UI_ADMIN_DESIGN.md` - admin UI design notes and browser-side ownership rules.

## Entry points

- Root `README.md` - short human entry point and quick start.
- Root `AGENTS.md` - minimal always-loaded routing file for all AI agents.
- `.skills/INDEX.md` - skill catalogue and selection guidance.

## Orchestrator product documents

- `ORCHESTRATOR_IDENTITY.md` - runtime-loaded product identity and operating role.
- `ORCHESTRATOR_AI.md` - AI risk-review behaviour, settings, approval gating, and logging rules.

## Sub-indexes

- `plan/INDEX.md` - durable planning hook and roadmap references.
- `research/INDEX.md` - research notes and implementation pattern cache.

## Tool-specific files

- Root `CLAUDE.md` - compatibility file for tools that read it.

## Maintenance rules

- Keep this index factual and short.
- Add new durable docs here.
- Do not paste long explanations into this index.
- If a document overlaps heavily with another, merge it or clearly mark one as historical.
