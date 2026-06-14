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

- VS Code app: Windows
- Runtime/backend: WSL Ubuntu
- Windows path: `E:\Programming\ai-tech-lead`
- WSL path: `/mnt/e/Programming/ai-tech-lead`
- Python: 3.13
- Package manager: uv

## Normal project run

Run from Ubuntu/WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead --task-id JH-001
```

For local development, add `--reload` to restart the process when `src/`, `config/`, or `docs/` change:

```bash
uv run python -m ai_tech_lead --task-id JH-001 --reload
```

Real coding-agent execution is disabled unless explicitly requested:

```bash
uv run python -m ai_tech_lead --task-id JH-001 --execute-coding-agent
```

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

Telegram bot credentials are read from the local `TELEGRAM_BOT_TOKEN` environment variable. You can place it in the project `.env` file for local development.

## LangGraph Studio run

Run from Ubuntu/WSL and leave the terminal running:

```bash
cd /mnt/e/Programming/ai-tech-lead
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

## Current focus

Start with a small local prototype that can receive explicit local tasks, create
controlled execution briefs, call one coding agent when explicitly enabled, and
pause for approval when needed. Telegram or similar chat input should be added as
an adapter around the core workflow, not baked into the graph.
