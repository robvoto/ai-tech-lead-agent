# Python Tooling

## Decision

Use Python 3.13 with uv.

Use uv as the project environment and dependency tool.

Do not use raw pip or manual virtual environment commands as the normal project workflow.

Do not add `requirements.txt` unless a specific tool or deployment path requires it.

## Current project runtime

Project Python is managed by uv.

Check it with:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python --version
```

Expected result:

```text
Python 3.13.x
```

The observed working runtime was:

```text
Python 3.13.13
```

## Important distinction

This command:

```bash
python3 --version
```

shows Ubuntu system Python. It may show:

```text
Python 3.12.3
```

That is not the project runtime.

This command:

```bash
uv run python --version
```

shows the project Python managed by uv.

## Expected project files

- `pyproject.toml` defines the project and dependencies.
- `.python-version` records the selected Python version.
- `.venv/` is the local environment managed by uv.
- `uv.lock` records exact resolved dependencies.

The uv environment folder is:

```text
/mnt/e/Programming/ai-tech-lead/.venv
```

Same folder from Windows:

```text
E:\Programming\ai-tech-lead\.venv
```

Do not create or repair `.venv` manually. Let uv manage it.

## Standard commands

From the WSL VS Code terminal:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv sync --link-mode=copy
uv run python --version
uv run python -m ai_tech_lead
```

## Add dependencies

Use uv:

```bash
uv add package-name
```

Example already used:

```bash
uv add langgraph langchain-core
```

Do not install project dependencies with raw pip as the normal workflow.

## LangGraph validation

Run:

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

Expected output:

```text
LangGraph OK
```

## uv hardlink warning

If uv prints a warning about failing to hardlink files and falling back to full copy, that is not a project failure.

This can happen because the project is on `/mnt/e` while uv cache/environment files may be on a different filesystem.

Use this to avoid the warning:

```bash
uv sync --link-mode=copy
```

## Principle

Keep dependency management reproducible and explicit.
