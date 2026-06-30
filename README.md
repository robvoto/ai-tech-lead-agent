# AI Tech Lead

Local AI Technical Lead Assistant project.

## Source of truth

Start with the documentation index:

```text
docs/INDEX.md
```

For current project direction and structure, read:

```text
docs/ARCHITECTURE.md
docs/GRAPH_WORKFLOW.md
docs/RUNTIME_RUNBOOK.md
docs/plan/INITIAL_PLAN.md
```

## Environment

- Runtime/backend: WSL Ubuntu
- Project root: `/home/robvoto/projects/ai-tech-lead`
- Python: 3.13
- Package manager: uv

## Agent Army entry point

AI Tech Lead can also be called by another local agent as a coding specialist through the non-interactive JSON subprocess contract. This path is for Agent Army / agent-to-agent routing and does not start Telegram or the admin UI.

```bash
cd /home/robvoto/projects/ai-tech-lead
uv run python -m ai_tech_lead run-agent-task --input-json input.json --output-json output.json
```

See `docs/ARCHITECTURE.md` for the Army contract and safety boundary.

## Telegram bot

AI Tech Lead also has a human-facing Telegram bot for direct use by you. When Telegram is enabled in local settings, the bot runs the coding-agent workflow interactively from chat:

- use it for direct coding requests from Telegram
- use `/run JH-###` to trigger a backlog item
- use `/code` for an explicit coding workflow

The Telegram bot is the direct human entry point; Agent Army is the non-interactive subprocess entry point.

## Normal project run

Run from Ubuntu/WSL:

```bash
cd /home/robvoto/projects/ai-tech-lead
uv run python -m ai_tech_lead --debug
```

For local development, add `--reload` to restart the process when `src/`, `config/`, or `docs/` change:

```bash
uv run python -m ai_tech_lead --debug --reload
```

Trigger backlog tasks through Telegram, for example:

```text
/run JH-001
```

Bootstrap a fresh workspace or inspect local health:

```bash
uv run python -m ai_tech_lead setup
uv run python -m ai_tech_lead doctor
uv run python -m ai_tech_lead knowledge-store stats
```

Coding-agent execution is controlled by the local settings/admin toggle (`execute_coding_agent`); there is no `--execute-coding-agent` CLI flag.

## Tests

Run the core test suite:

```bash
uv run pytest
```

The default tests should stay fast and should not call real coding agents.

## Local admin screen

Run from Ubuntu/WSL:

```bash
uv run python -m ai_tech_lead --admin
```

Or use the local helper script:

```bash
./run_admin.sh
```

Telegram bot credentials are read from the local `TELEGRAM_BOT_TOKEN` environment variable. You can place it in the project `.env` file for local development.

## LangGraph Studio run

Run from Ubuntu/WSL and leave the terminal running:

```bash
cd /home/robvoto/projects/ai-tech-lead
./run_langsmith.sh
```

## Dev tools

Install dev tools:

```bash
uv sync --group dev
```

Check formatting and linting:

```bash
uv run ruff check .
```

Format changed Python files only:

```bash
uv run ruff format <file-or-folder>
```

Fix safe lint issues:

```bash
uv run ruff check . --fix
```

Use dev commands on changed files where possible. Do not reformat the whole repository unless a separate cleanup task asks for it.
