# AI Tech Lead

Local AI Technical Lead Assistant project.

## Source of truth

Read this first for planning and current project direction:

```text
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
uv run python -m ai_tech_lead
```

## LangGraph Studio run

Run from Ubuntu/WSL and leave the terminal running:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run langgraph dev
```

Then open the APAC Studio connection documented in:

```text
docs/setup/03-first-run.md
```

## Current focus

Start with a small local prototype that can receive Telegram input, create controlled execution briefs, call one coding model, log token/cost usage, and pause for approval when needed.
