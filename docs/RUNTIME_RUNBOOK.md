# Runtime Runbook

Use this document for local run, test, and troubleshooting commands. Keep architecture decisions in `docs/ARCHITECTURE.md`.

## Runtime environment

- Windows project path: `E:\Programming\ai-tech-lead`
- WSL runtime path: `/mnt/e/Programming/ai-tech-lead`
- Python: 3.13
- Package manager: `uv`
- Normal runtime shell: WSL Ubuntu, not PowerShell

## Normal CLI run

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead --task-id JH-001
```

Real coding-agent execution is disabled unless explicitly enabled:

```bash
uv run python -m ai_tech_lead --task-id JH-001 --execute-coding-agent
```

## Admin UI

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead --admin
```

Default local admin URL:

```text
http://127.0.0.1:8766/
```

## LangGraph Studio

```bash
cd /mnt/e/Programming/ai-tech-lead
./run_langsmith.sh
```

Expected local API:

```text
http://127.0.0.1:2024
```

## Validation

Run the full test suite when needed:

```bash
uv run pytest
```

For focused instruction/prompt validation:

```bash
uv run pytest tests/test_instruction_assembler.py tests/test_prompt_loader.py -q
```

For linting:

```bash
uv run ruff check .
```

For formatting changed Python files only:

```bash
uv run ruff format <file-or-folder>
```

## Troubleshooting notes

- If a command works in WSL but not PowerShell, prefer WSL. This project runtime is WSL-first.
- If LangGraph Studio fails to import the graph, check `langgraph.json` and the exported `graph` object in `src/ai_tech_lead/coding_workflow_graph.py`.
- If coding-agent execution hangs or warns about sandbox dependencies, check the backlog item for Bubblewrap/Codex WSL setup notes before enabling real execution.
- If `/mnt/e` file access is slow, consider whether Windows-mounted filesystem latency is involved before blaming LangGraph.

## Maintenance rule

When run commands, ports, settings paths, or validation commands change, update this file and keep `README.md` short.
