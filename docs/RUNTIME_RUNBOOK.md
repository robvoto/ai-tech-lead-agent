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
uv run python -m ai_tech_lead backlog-sync-recover
uv run python -m ai_tech_lead run-audit <request_id>
```

`run-audit` prints the bounded local receipt for one coding run. It is useful for
debugging and audit review; it does not print checkpoint state, raw prompts, or
provider logs.

`backlog-sync-recover` runs a bounded recovery pass over backlog Sheet updates that
failed to sync (network/API errors during task completion). It also runs
automatically at Telegram-operator startup. Each pending update has a bounded
number of attempts (`backlog_pending_update_max_attempts` in settings); once
exhausted it is marked abandoned rather than retried forever or silently
dropped. Exit code is non-zero if anything is still pending, in conflict, or
abandoned after the pass.

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

A new task's input JSON must include `task`. Common optional fields are `request_id`, Hub
`run_id`, `source`, `project_root`, `project_reference`, `task_kind`,
`execution_mode`, and `backlog_reference`.

To resume a paused conversation, resubmit the same `request_id` with `decision:
{"option": "...", "text": "...", "actor": "..."}` instead of `task` — the option must be
one of the names the paused response's `pending_decision.options` reported. `text` is
required only when that option is marked `needs_text`.

- `backlog_reference` is an explicit pointer to one row in one Google Sheet backlog:
  `{"project_key": "...", "spreadsheet_id": "...", "sheet_name": "...", "item_id": "..."}`.
  `spreadsheet_id`/`sheet_name` may be omitted if `project_key` resolves through the
  local `backlog_projects` settings registry. An invalid or inaccessible reference
  fails the request clearly — there is no silent fallback to free-text-only mode.
- `task_kind` defaults to `coding_task`. Use `task_kind: "backlog_refinement"` when
  the caller wants AI Tech Lead to draft a new backlog item proposal instead of
  running the coding workflow.
- `project_reference` carries the generic caller-supplied target-project context that
  AI Tech Lead validates once and converts into its own immutable internal
  `TargetProjectContext`. For backlog refinement, the caller must supply backlog
  config through `project_reference.backlog` (or the legacy-compatible
  `project_reference.backlog_project`) so the runtime can resolve the right
  spreadsheet, sheet, item ID prefix, and column names without hardcoding them.
- A backlog-refinement request pauses for explicit human approval before any Sheet
  write. The paused response uses `status: "waiting_decision"` with
  `pending_decision.kind: "backlog_refinement_approval"` and `approve` / `cancel`
  options. If duplicate, obsolete, completed-equivalent, or conflicting-scope
  matches are found, the response ends as `needs_clarification` and does not queue
  a writable draft.
- If `project_reference.backlog` names the project's backlog location but the
  request only references an item by ID (e.g. `ATL-999`) without an explicit
  `backlog_reference`, AI Tech Lead fetches that item from the known location
  itself instead of pausing to ask where to find it. A row that isn't there is
  a definite answer, not a question for a human — the request ends clearly as
  `failed` rather than `needs_clarification`.
- If the backlog location isn't supplied at all but `project_reference.project_key`
  is, AI Tech Lead checks the local `settings.backlog_projects` registry for that
  key before asking a human — the same bounded registry `backlog_reference.project_key`
  already resolves against. A verified match is used exactly like an
  explicitly-supplied backlog location (above); no match, or no `project_key`
  at all, falls back to the clarification interrupt as before. This never
  guesses an item ID prefix, scans the filesystem, or touches another
  repository — only the already-loaded local settings.
- If both `backlog_reference` and `project_reference.backlog` are supplied,
  they must name the same spreadsheet/sheet. AI Tech Lead never silently
  picks one when they disagree — a mismatch fails the request clearly, before
  any Sheet is read. When they agree (or only one is supplied), the column
  layout from `project_reference.backlog.columns` (default column names
  otherwise) is used consistently for the initial fetch and for completion
  sync — never the default layout for one and a custom layout for the other.
- When a backlog item is resolved — either via an explicit `backlog_reference`
  up front, or via the known-backlog lookup above — and the run actually
  executes code successfully, AI Tech Lead writes the item's status/evidence
  columns (default `Status`/`Evidence / Validation`, or the project's custom
  names) back to the Sheet and reports the outcome in the response field
  `backlog_sync_status`: `not_applicable` (no item resolved, or only an
  instruction was produced), `synced`, `pending` (Sheet call failed, queued
  for recovery), `conflict` (the source row changed since it was fetched —
  nothing was overwritten), or `abandoned` (recovery attempts exhausted).
  Both resolution paths feed the same sync mechanism.

- When `run_id` is supplied, stdout is reserved for one structured progress JSON object per line. Runtime and coding-agent logs remain on stderr. The final result still goes only to `--output-json`.
- When `run_id` is omitted, the subprocess contract emits no progress lines to stdout.
- Progress events are bounded operational telemetry, not raw logs, prompts, plan text, provider output, or hidden reasoning.
- Safe default: omit `execution_mode` or set it to `instruction_only`.
- Use `execution_mode: execute` only when the caller explicitly wants coding-agent execution and
  local settings allow it.
- `project_root` is accepted only when it matches an authorised
  `settings.project_registry` entry, or when that exact root arrives with
  explicit human approval. Registered targets still fail closed when the
  platform, credentials, or location are unavailable. The same validation
  runs again on a resume call, since the graph is rebuilt each invocation.
- Subprocess responses use `success`, `needs_clarification`, `waiting_decision`, and `failed`.
- Every genuine pause inside the specialist workflow — approval, plan guidance, repeated
  failure guidance, research approval, backlog-refinement approval — is surfaced as
  `waiting_decision` with a `pending_decision` block describing what's paused and
  the named options available.
  `needs_clarification` is reserved for the one case with no conversation to resume: an
  unresolved reference, where the workflow ended rather than paused.
- External callers should read `uv run python -m ai_tech_lead manifest` and use the response
  fields `result_kind` and `pending_decision` instead of scraping `summary` text.
- Concurrent `run-agent-task` calls are allowed only when they do not collide on the
  same runtime lock scope. Duplicate `request_id` runs fail fast, and real coding-agent
  execution is locked per target `project_root`.

For a manual progress-contract check:

```bash
cat >/tmp/subprocess-task.json <<'JSON'
{
  "request_id": "req-demo-1",
  "run_id": "run-demo-1",
  "task": "Prepare a bounded instruction for a small documentation fix",
  "execution_mode": "instruction_only"
}
JSON

uv run python -m ai_tech_lead run-agent-task \
  --input-json /tmp/subprocess-task.json \
  --output-json /tmp/subprocess-result.json \
  1>/tmp/subprocess-progress.jsonl \
  2>/tmp/subprocess-runtime.log
```

`/tmp/subprocess-progress.jsonl` should contain only progress JSONL. The final result is in `/tmp/subprocess-result.json`.

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
