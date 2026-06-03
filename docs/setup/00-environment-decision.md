# Environment Decision

## Decision

Use the Windows VS Code application with the project opened in WSL mode.

Runtime commands must run from Ubuntu/WSL using the `/mnt/e/...` path.

## Confirmed working setup

Windows project path:

```text
E:\Programming\ai-tech-lead
```

WSL project path:

```text
/mnt/e/Programming/ai-tech-lead
```

Correct VS Code window indicator:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

Correct runtime terminal prompt shape:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

Wrong runtime terminal prompt shape:

```text
PS E:\Programming\ai-tech-lead>
```

PowerShell is not the project runtime terminal.

## Runtime rule

Use this from Ubuntu/WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead
```

Do not run project runtime commands from `E:\...` PowerShell.

## Python rule

Use uv-managed project Python.

Check project Python with:

```bash
uv run python --version
```

Expected:

```text
Python 3.13.x
```

Do not use plain `python3` as the project runtime check. That shows Ubuntu system Python.

## Future option

Later the repo may move to native WSL storage:

```text
~/projects/ai-tech-lead
```

Do not move it unless explicitly requested.
