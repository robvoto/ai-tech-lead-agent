---
name: backlog-management
description: Use for backlog loading, parsing, selection, status, evidence, and task handoff rules.
---

# Skill: Backlog Management

Use when creating, parsing, selecting, updating, or explaining backlog items.

## Rules
- Do not let the agent silently choose work in a hidden way.
- Normal execution should select a backlog item by explicit ID.
- Demo-only selection may exist only when clearly named as demo behavior, for example `load_first_backlog_item_for_demo`.
- Backlog parsing should expose clear errors for missing files, missing headings, duplicate IDs, or unknown requested IDs.
- Each executable backlog item must include explicit `Approval Required` and `Approval Reason` fields.
- Do not delete backlog items without human approval.
- Do not mark work done without validation evidence.
- Preserve the human's original intent when formatting or converting backlog items.
- Do not describe an item's status with vague, non-committal words like "defer" or "deprioritize" — in `Notes / Cleanup Action` and in explanations to the human, state the concrete reason it isn't next and the specific condition that would change that.

## Current backlog ownership
- The Google Sheet listed in `docs/INDEX.md` is the only canonical backlog. `SheetsBacklogRepository` (`backlog_sheets_repository.py`) is the live repository behind every read/write — Telegram, chat tools, and the Hub subprocess path all go through it.
- The shared backlog access identity is `agent-backlog-access@robvoto-agent-platform-iam.gserviceaccount.com`; it should have access to all relevant project backlog spreadsheets. Do not infer that other configured service accounts are prohibited.
- `data/backlog.sqlite3` is runtime state only (task snapshots, pending-update outbox, sync conflicts — `backlog_runtime_store.py`). It has no create/edit/list/planning methods; never treat it as a backlog source.
- Only the `Status` and `Evidence / Validation` columns are ever written by runtime code.
- A completed run always writes ahead to the outbox first, then attempts an immediate flush. If the source row changed since the item was fetched, the update is abandoned and a conflict is recorded — never a silent overwrite of a human edit. A failed Sheets call stays pending for bounded recovery: `uv run python -m ai_tech_lead backlog-sync-recover` (also runs automatically at Telegram-operator startup).
- Normal task selection uses Telegram, for example `/run ATL-001`.
- There is no `--task-id` CLI flag in the current app entrypoint.
- The old Markdown backlog format is historical only; do not author new items in that format unless a targeted migration task explicitly requires it.

## Hub boundary
- An external caller may ask AI Tech Lead to work on an explicit task. To have AI Tech Lead also close out a specific backlog item on completion, the caller supplies `backlog_reference` in the subprocess input: `{"project_key": "...", "spreadsheet_id": "...", "sheet_name": "...", "item_id": "..."}` (`spreadsheet_id`/`sheet_name` may be omitted if `project_key` resolves via the local `backlog_projects` settings registry). The response's `backlog_sync_status` field reports the outcome (`not_applicable`, `synced`, `pending`, `conflict`, `abandoned`).
- Do not add silent alternate backlog sources or let caller-side orchestration redefine backlog truth. Every reference is caller-supplied and explicit — no autonomous backlog discovery.

## Finish format
Report:
- Backlog item selected or changed
- Selection rule used
- Validation evidence
