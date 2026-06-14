# Backlog Management

## Current backlog location

The current working backlog is the Excel workbook:

```text
data/backlog/ai_tech_lead_backlog.xlsx
```

From WSL, the same file is available at:

```text
/mnt/e/Programming/ai-tech-lead/data/backlog/ai_tech_lead_backlog.xlsx
```

The workbook was created from the previous Markdown backlog and contains structured sheets for backlog items, quality checks, dashboard summaries, and schema guidance.

## Legacy Markdown backlog

`docs/BACKLOG.md` is now a historical/source backup, not the preferred working format.

It is still useful for:

- tracing original backlog wording;
- recovering from spreadsheet mistakes;
- comparing migration results;
- keeping git-readable history while the Excel-backed backlog is being evaluated.

Do not add new backlog items to `docs/BACKLOG.md` unless the task is explicitly about migration, repair, or historical documentation.

## Current editing rule

The Excel-backed backlog repository is implemented, but backlog changes must still be handled carefully:

- The human operator owns backlog decisions.
- The orchestrator may propose or apply backlog edits only when explicitly asked.
- Worker coding agents must not freely edit the workbook.
- If a coding-agent handoff needs a backlog update, the instruction must say exactly which row/field is allowed to change.
- Status changes should remain human-approved unless the task is explicitly documentation/backlog maintenance.

## Tooling state

The workbook can be seen by this environment at the project path above.

The current project Python environment includes `openpyxl`, so code that reads or writes `.xlsx` files can use the workbook through the repository boundary.

Use the backlog repository boundary for routine edits. If that layer is not appropriate, use an explicitly approved alternative editing method.

Do not assume spreadsheet editing is a free-form bulk-edit operation. Use the repository boundary and approved workflows.

## Future direction

Google Sheets remains a possible future backlog source.

Before Google Sheets, the next safer step is to keep the Excel-backed backlog repository behind the same boundary used by the current backlog loader. The graph, Telegram operator, and coding-agent handoff logic should depend on the backlog repository interface, not on the file format.

## Migration note

The Excel workbook exposed quality issues in the Markdown backlog, including missing `Status`, inconsistent metadata, and uneven detail across items. The workbook is now the working structure, while `docs/BACKLOG.md` remains a historical/source backup.
