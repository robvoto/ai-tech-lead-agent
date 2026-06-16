# Runtime Runbook

Use this document for local run, test, and troubleshooting commands. Keep architecture decisions in `docs/ARCHITECTURE.md`.

## Runtime environment

- Windows project path: `E:\Programming\ai-tech-lead`
- WSL runtime path: `/mnt/e/Programming/ai-tech-lead`
- Python: 3.13
- Package manager: `uv`
- Normal runtime shell: WSL Ubuntu, not PowerShell
- Supported WSL distro for Codex sandboxing: Ubuntu with `apt` available.
- Codex sandboxing in WSL requires `bubblewrap` (`bwrap`) to be on `PATH`.

## Normal CLI run

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead --debug
```

For local development, use `--reload` to restart the process when source, config, or prompt files change:

```bash
uv run python -m ai_tech_lead --debug --reload
```

Trigger backlog tasks through Telegram, for example:

```text
/run JH-001
```

Coding-agent execution is controlled by the local settings/admin toggle (`execute_coding_agent`); there is no `--execute-coding-agent` CLI flag.

## Agent Army subprocess

The Agent Army calls AI Tech Lead directly through the JSON subprocess entrypoint:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead run-agent-task --input-json /tmp/army-task.json --output-json /tmp/army-result.json
```

The input JSON must include `task`. Common optional fields are `request_id`, `source`,
`project_root`, `execution_mode`, `human_approved`, and `approval_token`.

- Safe default: omit `execution_mode` or set it to `instruction_only`.
- Use `execution_mode: execute` only when the army explicitly wants coding-agent execution and
  local settings allow it.
- `project_root` is accepted only when it matches one of `settings.army_allowed_project_roots`.
- If `human_approved` is true, the request must include the matching one-time `approval_token`
  previously issued for the same `request_id` and task.

## Admin UI

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead --admin
```

Helper script:

```bash
./run_admin.sh
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
- If coding-agent execution warns that Bubblewrap is missing or hangs during startup, verify `command -v bwrap` and `bwrap --version` in WSL before retrying Codex.
- On Ubuntu WSL, install it with `sudo apt update && sudo apt install bubblewrap`, then re-run `command -v bwrap` and `bwrap --version`.
- If `/mnt/e` file access is slow, suspect Windows-mounted filesystem latency. Move the repo to native Linux storage, such as `~/ai-tech-lead`, when you need faster Codex and file-system performance.
- If you keep the repo on `/mnt/e`, keep Codex runs short and avoid extra file scanning while debugging latency.

## Maintenance rule

When run commands, ports, settings paths, or validation commands change, update this file and keep `README.md` short.
