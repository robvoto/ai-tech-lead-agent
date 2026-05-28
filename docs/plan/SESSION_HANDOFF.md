# Session Handoff

## Project root

Windows:

```text
E:\Programming\ai-tech-lead
```

WSL:

```text
/mnt/e/Programming/ai-tech-lead
```

Durable planning file:

```text
docs/plan/INITIAL_PLAN.md
```

Contributor instructions:

```text
AGENTS.md
```

## Project direction

Build a local AI Technical Lead Assistant.

The assistant should supervise Codex, Claude Code, and Gemini rather than replace them.

Main purpose: prevent coding-agent failure modes such as doing too much, unsafe changes, unapproved fallbacks, hardcoded sample heuristics, dead code, missing help text/docstrings, and claims without evidence.

## Confirmed environment

Use Windows VS Code app opened in WSL mode.

Correct VS Code window indicator:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

Correct terminal prompt:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

Do not use PowerShell for project runtime commands.

Open correctly from WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
code .
```

VS Code needs the Microsoft WSL extension and Microsoft Python extension installed in the WSL window.

## Python/tooling decision

Use Python 3.13 with uv.

Project Python is managed by uv. Do not use plain `python3` for project runs.

Key commands:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv sync --link-mode=copy
uv run python --version
uv run python -m ai_tech_lead
```

Expected Python:

```text
Python 3.13.x
```

Observed working runtime:

```text
Python 3.13.13
```

Ubuntu system Python may show 3.12.3. That is not the project runtime.

The uv environment is:

```text
/mnt/e/Programming/ai-tech-lead/.venv
```

The interpreter is:

```text
/mnt/e/Programming/ai-tech-lead/.venv/bin/python
```

## Dependencies

Use `pyproject.toml` and `uv.lock`.

Do not use `requirements.txt` unless a specific tool/deployment path requires it.

Current dependencies:

```text
langgraph
langchain-core
```

Validate LangGraph:

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

## Git/SSH status

Use Git over SSH from WSL.

SSH keys live in:

```text
/home/robvoto/.ssh
```

GitHub SSH auth worked with:

```bash
ssh -T git@github.com
```

Successful result shape:

```text
Hi robvoto! You've successfully authenticated, but GitHub does not provide shell access.
```

Check remote:

```bash
git remote -v
```

Remote must use real GitHub owner, not placeholder `USERNAME`.

## Current code state

Project runs with:

```bash
uv run python -m ai_tech_lead
```

Main package is under:

```text
src/ai_tech_lead
```

Do not run `uv run python main` or `uv run python -m main`; the package entry point is:

```bash
uv run python -m ai_tech_lead
```

A first LangGraph toy graph exists:

```text
src/ai_tech_lead/graph_sample.py
```

It demonstrates:

- `State` using `TypedDict`
- nodes as Python functions
- node names as strings
- simple edges
- conditional edges
- compile
- invoke

The current graph is still a learning sample. Next improvement is to make it project-shaped:

```text
request -> create_brief -> finish
```

No LLM yet.

## Learning path

Use LangChain Academy as learning guide:

```text
E:\Programming\langchain-academy
```

Relevant lesson:

```text
module-1/simple-graph.ipynb
```

Learn these first:

- State
- Node
- Edge
- Conditional edge
- Compile
- Invoke

Use the pattern:

1. Explain concept in plain English.
2. Map to Academy lesson.
3. Code tiny version.
4. Run validation.
5. Explain result.

## Documentation cleanup

`docs/setup/01-wsl-vscode-setup.md` was rewritten as a runbook.

`docs/setup/02-python-tooling.md` was being rewritten as a runbook when the conversation became slow.

Next documentation task:

- finish/check `docs/setup/02-python-tooling.md`
- rewrite `docs/setup/03-first-run.md` as runbook
- remove/merge `docs/setup/04-git-remote-setup.md` if still present

## Important communication preference

Avoid noisy history-style docs.

Docs should be professional runbooks:

```text
Step 1: do this
Expected result: this
If it fails: this means X
```

Do not add fallback commentary unless there is actual evidence from a failed command or dependency conflict.
