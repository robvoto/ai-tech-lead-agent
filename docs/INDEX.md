# Documentation Index

Use this file for progressive disclosure. Start here, then open only the document needed for the current task.

## Canonical project standards

- External standards source of truth: https://docs.google.com/document/d/1VDlwjmfUwGA-eveXeHD7rBJ4tRRBafloQwj62KWU3Q0/edit?tab=t.0
- If the standards document cannot be accessed, stop and ask the operator for the current exported text. Do not guess standards updates.

## Core project documents

- `ARCHITECTURE.md` - system architecture, module boundaries, adapter strategy, persistence, and growth rules.
- `GRAPH_WORKFLOW.md` - implemented LangGraph workflow, nodes, routes, interrupts, and execution boundary.
- `RUNTIME_RUNBOOK.md` - local run, test, Studio, admin, and troubleshooting commands.
- `ARMY_INTEGRATION.md` - Agent Army / agent-to-agent subprocess contract, JSON input/output shape, and Telegram boundary.
- `CONTEXT_MANAGEMENT.md` - context allowed in graph state, prompts, Telegram messages, logs, and coding-agent handoffs.
- `BACKLOG_MANAGEMENT.md` - current backlog location, Excel workbook rules, and migration notes.
- `BACKLOG.md` - archived Markdown backlog source/backup retained for history and comparison at `data/backlog/archive/BACKLOG.md`; the current workbook lives at `data/backlog/ai_tech_lead_backlog.xlsx`.
- `UI_ADMIN_DESIGN.md` - admin UI design notes and browser-side ownership rules.

## Entry points

- Root `README.md` - short human entry point and quick start.
- Root `AGENTS.md` - minimal always-loaded routing file for all AI agents.
- `.skills/INDEX.md` - skill catalogue and selection guidance.

## Orchestrator product documents

- `ORCHESTRATOR_IDENTITY.md` - runtime-loaded product identity and operating role.
- `ORCHESTRATOR_AI.md` - AI risk-review behaviour, settings, safety fallback, and logging rules.

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
