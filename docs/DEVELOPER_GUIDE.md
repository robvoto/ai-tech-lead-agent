# Developer Guide

This repository uses `uv` for the normal application workflow, and Ruff is installed through the
project's dev dependency group for formatting and linting.

## Dev Tools

Install the development tools into the existing project environment:

```bash
uv sync --group dev
```

Check formatting and linting:

```bash
uv run ruff check .
```

Format changed Python files:

```bash
uv run ruff format <file-or-folder>
```

Fix safe lint issues:

```bash
uv run ruff check . --fix
```

Use these commands on the files you changed rather than reformatting the whole repository unless a
separate cleanup task explicitly asks for that.
