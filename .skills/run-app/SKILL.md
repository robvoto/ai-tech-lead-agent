---
name: run-app
description: Launch the AI Tech Lead bot for manual testing or verification.
---

# Skill: Run App

## Launch command

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead --debug
```

The app runs until interrupted (Ctrl-C). There is no `--task-id` CLI flag; task execution is triggered via Telegram (e.g. `/run ATL-001`).

## Background launch (for automated checks)

```bash
uv run python -m ai_tech_lead --debug > /tmp/atl_run.log 2>&1 &
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
