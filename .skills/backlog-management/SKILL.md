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
- Preserve the human’s original intent when formatting or converting backlog items.

## Current local backlog ownership
- Human planning backlog decisions live in the Google Sheet listed in `docs/INDEX.md`.
- Runtime reads and updates the local SQLite backlog at `data/backlog.sqlite3`.
- Normal task selection uses Telegram, for example `/run ATL-001`.
- There is no `--task-id` CLI flag in the current app entrypoint.
- The old Markdown backlog format is historical only; do not author new items in that format unless a targeted migration task explicitly requires it.

## Hub boundary
- An external caller may ask AI Tech Lead to work on an explicit task, but backlog ownership stays local to this repo.
- Do not add silent alternate backlog sources or let caller-side orchestration redefine backlog truth.

## Future selection direction
Additional input adapters may select tasks through explicit mechanisms:
- Web form or human approval list, where the agent shows candidate tasks and waits for one choice.

## Finish format
Report:
- Backlog item selected or changed
- Selection rule used
- Validation evidence
