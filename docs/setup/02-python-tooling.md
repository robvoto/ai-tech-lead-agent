# Python Tooling Runbook

## Purpose

Define the project Python and dependency workflow.

Use:

- Python 3.13
- uv
- `pyproject.toml`
- `uv.lock`

Do not use raw pip, manual virtual environments, or `requirements.txt` as the normal project workflow.

## Step 1: Open the WSL project terminal

Correct prompt shape:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

Wrong prompt shape:

```text
PS E:\Programming\ai-tech-lead>
```

PowerShell is not the project runtime terminal.

## Step 2: Go to the project root

```bash
cd /mnt/e/Programming/ai-tech-lead
pwd
```

Expected:

```text
/mnt/e/Programming/ai-tech-lead
```

## Step 3: Check uv

```bash
uv --version
```

Expected result shape:

```text
uv 0.x.x
```

## Step 4: Check the pinned Python version

```bash
cat .python-version
```

Expected:

```text
3.13
```

If this is missing or wrong, pin it again:

```bash
uv python pin 3.13
```

## Step 5: Check the Python constraint

```bash
grep requires-python pyproject.toml
```

Expected:

```text
requires-python = ">=3.13,<3.14"
```

This prevents the project from drifting back to Python 3.14.

## Step 6: Sync dependencies

```bash
uv sync --link-mode=copy
```

Use `--link-mode=copy` because the project is on `/mnt/e`, a Windows-mounted drive. This avoids uv hardlink warnings across filesystems.

## Step 7: Check the project Python

```bash
uv run python --version
```

Expected:

```text
Python 3.13.x
```

Observed working runtime:

```text
Python 3.13.13
```

Do not use `python3 --version` as the project runtime check. That shows Ubuntu system Python.

## Step 8: Check the uv-managed environment

```bash
uv run which python
```

Expected shape:

```text
/mnt/e/Programming/ai-tech-lead/.venv/bin/python
```

The environment folder is:

```text
/mnt/e/Programming/ai-tech-lead/.venv
```

Do not create, activate, or repair `.venv` manually as the normal workflow.

## Step 9: Add dependencies

Use uv:

```bash
uv add package-name
```

Current core dependencies:

```text
langgraph
langchain-core
```

These are recorded in `pyproject.toml` and locked in `uv.lock`.

## Step 10: Validate LangGraph runtime import

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

Expected:

```text
LangGraph OK
```

## Step 11: Run the project

```bash
uv run python -m ai_tech_lead
```

Expected result shape:

```text
AI Technical Lead Assistant started
Project root detected: /mnt/e/Programming/ai-tech-lead
SQLite ready: /mnt/e/Programming/ai-tech-lead/data/ai_tech_lead.sqlite3
```

## Troubleshooting

### `uv: command not found`

You are either in the wrong terminal or uv is not installed in WSL.

First check the prompt. Use WSL, not PowerShell.

### `python3 --version` shows Python 3.12.3

That is Ubuntu system Python. It is not the project runtime.

Use:

```bash
uv run python --version
```

### VS Code shows Windows Python or Python 3.14

The VS Code window is not using the WSL project interpreter.

Open the project from WSL with:

```bash
cd /mnt/e/Programming/ai-tech-lead
code .
```

Then select the `.venv` interpreter from the WSL window.

### LangGraph imports in terminal but VS Code shows a red underline

The runtime is correct. Fix VS Code interpreter selection in the WSL window.
