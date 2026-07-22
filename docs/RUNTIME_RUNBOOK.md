# Runtime Runbook

Use this document for local run, test, and troubleshooting commands. Keep architecture decisions in `docs/ARCHITECTURE.md`.

## Runtime environment

- Project root: `/home/robvoto/projects/ai-tech-lead`
- Python: 3.13
- Package manager: `uv`
- Normal runtime shell: WSL Ubuntu, not PowerShell
- Supported WSL distro for Codex sandboxing: Ubuntu with `apt` available.
- Codex sandboxing in WSL requires `bubblewrap` (`bwrap`) to be on `PATH`.

## Local settings bootstrap

Runtime settings are local machine state and are loaded from:

```text
data/coding_agent_settings.json
```

That real file is intentionally ignored by Git because it can contain local paths, Telegram chat IDs, and operator toggles. To create it in WSL on a fresh clone:

```bash
cd /home/robvoto/projects/ai-tech-lead
cp data/coding_agent_settings.example.json data/coding_agent_settings.json
```

Then edit `data/coding_agent_settings.json` locally if Telegram, execution mode, allowed project roots, or model settings need to change.

The settings file now also carries the explicit knowledge-store path. The current default keeps that store inside the project `data/` directory so the path is visible and easy to validate.

## Normal CLI run

```bash
cd /home/robvoto/projects/ai-tech-lead
uv run python -m ai_tech_lead --debug
```

For local development, use `--reload` to restart the process when source, config, docs, or prompt files change:

```bash
uv run python -m ai_tech_lead --debug --reload
```

Trigger backlog tasks through Telegram, for example:

```text
/run ATL-001
```

Telegram is the human-facing bot for the AI Tech Lead coding agent. Use it when you want to interact with the coding workflow directly from chat.

Workspace bootstrap and local health checks:

```bash
cd /home/robvoto/projects/ai-tech-lead
uv run python -m ai_tech_lead setup
uv run python -m ai_tech_lead doctor
uv run python -m ai_tech_lead bootstrap-project-pack /tmp/new-target-repo
uv run python -m ai_tech_lead knowledge-store stats
uv run python -m ai_tech_lead knowledge-store backup /tmp/knowledge_store.sqlite3.bak
uv run python -m ai_tech_lead knowledge-store restore /tmp/knowledge_store.sqlite3.bak
uv run python -m ai_tech_lead knowledge-store compact
```

`bootstrap-project-pack` is an explicit operator action that writes a small starter
`AGENTS.md`, `docs/INDEX.md`, and `.skills/` pack into a reviewed target repo.
It refuses to overwrite existing files unless `--overwrite` is supplied.

Coding-agent execution is controlled by the local settings/admin toggle (`execute_coding_agent`); there is no `--execute-coding-agent` CLI flag.

## JSON subprocess

AI Tech Lead can be called directly through the non-interactive JSON subprocess entrypoint:

```bash
cd /home/robvoto/projects/ai-tech-lead
uv run python -m ai_tech_lead run-agent-task --input-json /tmp/subprocess-task.json --output-json /tmp/subprocess-result.json
```

The input JSON must include `task`. Common optional fields are `request_id`, `source`,
`project_root`, `execution_mode`, `human_approved`, and `approval_token`.

- Safe default: omit `execution_mode` or set it to `instruction_only`.
- Use `execution_mode: execute` only when the caller explicitly wants coding-agent execution and
  local settings allow it.
- `project_root` is accepted only when it matches one of `settings.allowed_project_roots`.
- If `human_approved` is true, the request must include the matching one-time `approval_token`
  previously issued for the same `request_id` and task.
- Subprocess responses use `success`, `needs_clarification`, `approval_required`, and `failed`.
- Human-text pauses inside the specialist workflow, such as plan guidance or repeated failure guidance, are surfaced here as `needs_clarification`.
- External callers should read `uv run python -m ai_tech_lead manifest` and use the response fields `result_kind`, `caller_action`, `resume_supported`, `resume_fields`, and `interrupt_kind` instead of scraping `summary` text.

## Admin UI

```bash
cd /home/robvoto/projects/ai-tech-lead
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
cd /home/robvoto/projects/ai-tech-lead
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

## Logs

Runtime logs are written under:

```text
logs/ai_tech_lead.runtime.log
logs/ai_tech_lead.coding_agent.log
```

- `ai_tech_lead.runtime.log` contains the orchestrator, graph, Telegram, admin, and research logs.
- `ai_tech_lead.coding_agent.log` contains raw coding-agent subprocess output.
- `--debug` raises console and runtime-log verbosity and includes research scoring diagnostics.

## Troubleshooting notes

- If startup says `Settings file not found`, create `data/coding_agent_settings.json` from `data/coding_agent_settings.example.json`.
- If a command works in WSL but not PowerShell, prefer WSL. This project runtime is WSL-first.
- If you need to debug research selection or workflow routing, inspect `logs/ai_tech_lead.runtime.log` first, then `logs/ai_tech_lead.coding_agent.log` for subprocess output details.
- If LangGraph Studio fails to import the graph, check `langgraph.json` and the exported `graph` object in `src/ai_tech_lead/coding_workflow_graph.py`.
- If coding-agent execution warns that Bubblewrap is missing or hangs during startup, verify `command -v bwrap` and `bwrap --version` in WSL before retrying Codex.
- On Ubuntu WSL, install it with `sudo apt update && sudo apt install bubblewrap`, then re-run `command -v bwrap` and `bwrap --version`.
- If a Windows-mounted filesystem is slow, suspect filesystem latency. Keep the repo on native Linux storage, such as `/home/robvoto/projects/ai-tech-lead`, when you need faster Codex and file-system performance.
- If you keep the repo on a Windows-mounted filesystem, keep Codex runs short and avoid extra file scanning while debugging latency.

## Maintenance rule

When run commands, ports, settings paths, or validation commands change, update this file and keep `README.md` short.
