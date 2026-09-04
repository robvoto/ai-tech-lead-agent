---
name: run-app
description: Launch the AI Tech Lead bot for manual testing or verification.
---

# Skill: Run App

## Launch command

Run from any AI Tech Lead worktree with the tracked launcher:

```bash
./scripts/run-worktree.sh --debug
```

The launcher resolves the current worktree dynamically, reuses the primary worktree's `.venv` when needed, forces imports from the current worktree's `src`, and fails before startup if Python imports AI Tech Lead from another worktree. If the ignored local settings file is missing in a linked worktree, it creates a worktree-local copy from the primary settings and rewrites only the primary AI Tech Lead root to the current worktree root.

Do not launch a linked worktree with `../ai-tech-lead/.venv/bin/python -m ai_tech_lead` directly: the shared editable install points at the primary worktree unless `PYTHONPATH` is corrected.

The app runs until interrupted (Ctrl-C). There is no `--task-id` CLI flag; task execution is triggered via Telegram (e.g. `/run ATL-001`).

## Background launch (for automated checks)

```bash
./scripts/run-worktree.sh --debug > /tmp/atl_run.log 2>&1 &
APP_PID=$!
sleep 20
cat /tmp/atl_run.log
kill $APP_PID 2>/dev/null
```

## Healthy startup indicators

- `SQLite ready: ...`
- `Telegram command menu registered: [...]`
- `Telegram polling started`

## Common startup issues

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` on admin server | Port 8766 already occupied | Kill existing process or ignore (admin server non-critical) |
| `BOT_COMMAND_INVALID` in `setMyCommands` | Telegram command name contains hyphen | Command names must be `[a-z0-9_]` only |

## Interacting via Telegram

Send bot commands directly in the Telegram chat:
- `/run ATL-001` — run a backlog item
- `/status` — check bot status
- `/list` — list open backlog items
- `/cancel_code` — stop a running coding agent
- `/set_status ATL-001 Done` — update backlog status
