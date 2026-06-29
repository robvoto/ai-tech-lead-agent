# Backlog Management

## Canonical project standards

Before changing backlog structure, workflow rules, documentation, runtime behaviour, or automation, check the current project standards source of truth:

- https://docs.google.com/document/d/1VDlwjmfUwGA-eveXeHD7rBJ4tRRBafloQwj62KWU3Q0/edit?tab=t.0

If that document cannot be accessed, stop and ask the operator for the current exported text. Do not guess standards updates.

## Current backlog sources

Operator-facing AI agents backlog:

```text
https://docs.google.com/spreadsheets/d/1-e2lQ6vLUD8A5t3cuLhrjRvdTbs3hfs4ptdEE2yDaEc/edit
```

This Google Sheet is the current human planning backlog for AI-agent / AI Tech Lead data, setup, production, and knowledge-store work.

Runtime backlog source:

```text
data/backlog.sqlite3
```

The running app reads runtime backlog items from SQLite through `SqliteBacklogRepository`. Do not assume that editing the Google Sheet or a local workbook changes the running app.

Local Excel workbook path:

```text
data/backlog/ai_tech_lead_backlog.xlsx
```

WSL path:

```text
/home/robvoto/projects/ai-tech-lead/data/backlog/ai_tech_lead_backlog.xlsx
```

The local Excel workbook must not be treated as current unless it has been checked against the Google Sheet and runtime SQLite backlog. The backlog item `ATL-DATA-002 - Reconcile ai_tech_lead_backlog.xlsx, Google Sheet, and runtime SQLite` exists for that work. Do not claim the workbook is up to date without evidence from WSL.

## Current editing rule

- The human operator owns backlog decisions.
- New planning items for AI agents should be added to the Google Sheet above.
- Runtime execution still depends on SQLite until a proper sync/reconcile workflow is implemented.
- Worker coding agents must not freely edit the workbook, SQLite database, or Google Sheet.
- If a coding-agent handoff needs a backlog update, the instruction must say exactly which source, row/item, and field is allowed to change.
- Status changes should remain human-approved unless the task is explicitly documentation/backlog maintenance.

## Archived Markdown backlog

`data/backlog/archive/BACKLOG.md` is historical only, not the working backlog.

It may be used only for:

- tracing original wording;
- comparing migration results;
- recovering historical context.

Do not add new backlog items to `data/backlog/archive/BACKLOG.md` unless the task is explicitly about archive repair or historical documentation.

## No hidden alternate paths

Do not add hidden alternate backlog sources, hidden compatibility paths, or silent source switching.

If the intended backlog source is missing, stale, inaccessible, or inconsistent, stop and ask the operator. Report the exact source that was checked and the evidence found.

## Tooling state

Routine runtime edits must go through the backlog repository boundary. Planning edits go into the Google Sheet. Local workbook changes require explicit reconciliation with SQLite before they can affect runtime execution.

Do not assume spreadsheet or database editing is a free-form bulk-edit operation. Use the approved source and approved workflow for the task.
