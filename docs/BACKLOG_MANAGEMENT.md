# Backlog Management

## Canonical project standards

Before changing backlog structure, workflow rules, documentation, runtime behaviour, or automation, check the current project standards source of truth:

- https://docs.google.com/document/d/1VDlwjmfUwGA-eveXeHD7rBJ4tRRBafloQwj62KWU3Q0/edit?tab=t.0

If that document cannot be accessed, stop and ask the operator for the current exported text. Do not guess standards updates.


## Current backlog location

The current runtime backlog source is SQLite:

```text
data/backlog.sqlite3
```

The structured Excel workbook is still maintained as the human-review/planning workbook:

```text
data/backlog/ai_tech_lead_backlog.xlsx
```

From WSL, the same workbook is available at:

```text
/home/robvoto/projects/ai-tech-lead/data/backlog/ai_tech_lead_backlog.xlsx
```

Important: do not edit the Excel workbook alone when the running app must see the change. Runtime currently reads SQLite through `SqliteBacklogRepository`; Excel is not automatically re-synced after the SQLite database already exists. Until a dedicated sync/reconcile workflow exists, any manual Excel update must be mirrored into SQLite or applied through the runtime repository path.

The workbook was created from the previous Markdown backlog and contains structured sheets for backlog items, quality checks, dashboard summaries, and schema guidance.

## Archived Markdown backlog

`data/backlog/archive/BACKLOG.md` is now a historical/source backup, not the preferred working format.

It is still useful for:

- tracing original backlog wording;
- recovering from spreadsheet mistakes;
- comparing migration results;
- keeping git-readable history while the Excel-backed backlog is being evaluated.

Do not add new backlog items to `data/backlog/archive/BACKLOG.md` unless the task is explicitly about migration, repair, or historical documentation.

## Current editing rule

The SQLite-backed runtime repository is implemented, and the Excel workbook remains the human-review workbook. Backlog changes must still be handled carefully:

- The human operator owns backlog decisions.
- The orchestrator may propose or apply backlog edits only when explicitly asked.
- Worker coding agents must not freely edit the workbook.
- If a coding-agent handoff needs a backlog update, the instruction must say exactly which row/field is allowed to change.
- Status changes should remain human-approved unless the task is explicitly documentation/backlog maintenance.

## Tooling state

The SQLite database and workbook can be seen by this environment at the project paths above.

Use the backlog repository boundary for routine edits. If that layer is not appropriate, use an explicitly approved alternative editing method.

Do not assume spreadsheet or database editing is a free-form bulk-edit operation. Use the repository boundary and approved workflows.

## Future direction

Google Sheets remains a possible future backlog source.

Before Google Sheets, the next safer step is to remove ambiguity between the SQLite runtime backlog and the Excel review workbook. The graph, Telegram operator, and coding-agent handoff logic should depend on the backlog repository interface, not on the file format.

## Migration note

The Excel workbook exposed quality issues in the Markdown backlog, including missing `Status`, inconsistent metadata, and uneven detail across items. The workbook is now the working structure, while `data/backlog/archive/BACKLOG.md` remains a historical/source backup.
