# AI Tech Lead

Local AI Technical Lead Assistant project.

## Source of truth

Read these first for current project direction and structure:

```text
docs/ARCHITECTURE.md
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

## LangGraph Studio run

Run from Ubuntu/WSL and leave the terminal running:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run langgraph dev
```

## Current focus

Start with a small local prototype that can receive explicit local tasks, create
controlled execution briefs, call one coding agent when explicitly enabled, and
pause for approval when needed. Telegram or similar chat input should be added as
an adapter around the core workflow, not baked into the graph.
