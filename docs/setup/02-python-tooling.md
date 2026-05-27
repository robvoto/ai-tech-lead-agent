# Python Tooling

## Decision

Use Python 3.13 with uv.

Installed runtime:

cpython-3.13-linux-x86_64-gnu

Project command runner:

uv

## Project rule

Use uv as the project environment and dependency tool.

Do not use raw pip or manual virtual environment commands as the normal project workflow.

## Initial tooling

## Expected project files
- Python 3.13
- Virtual environment
- uv
- SQLite

- `pyproject.toml` defines the project and dependencies.
- `.python-version` records the selected Python version.
- `.venv/` is the local environment managed by uv.
- `uv.lock` records exact resolved dependencies.

## Standard commands

From WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv python pin 3.13
uv sync
uv run python -m ai_tech_lead
```

## uv hardlink warning

If uv prints a warning about failing to hardlink files and falling back to full copy, that is not a project failure.

This can happen because the project is on `/mnt/e` while uv cache/environment files may be on a different filesystem.

To suppress the warning for this project, use:

```bash
export UV_LINK_MODE=copy
```

or run uv with:

```bash
uv sync --link-mode=copy
```

## Principle

Keep dependency management reproducible and explicit.
Other dependencies will be added only when needed.
