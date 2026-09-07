# Architecture

This project is a local AI technical lead orchestrator. The core product is not
the admin UI or Telegram surface; it is the workflow that turns an explicit
human request into a bounded coding-agent execution.

## Document split

Use this file for stable system boundaries: ownership, adapters, persistence,
contracts, and module responsibilities.

Use `GRAPH_WORKFLOW.md` for the node-by-node runtime path of the main coding
workflow graph.

## Design Diagram

This diagram is maintained as Mermaid text so it can evolve with the code. It is not a one-off drawing.

```mermaid
flowchart TD
    Human[Human Operator]
    Telegram[Telegram Long Polling Adapter]
    Admin[Local Admin UI]
    Caller[External Caller / Local Automation]
    CallerRunner[JSON subprocess adapter<br/>run-agent-task]

    Backlog[Backlog Loader]
    Settings[Settings JSON + Validation]
    Graph[LangGraph Coding Workflow]
    Research[Research Gate + Bounded Docs Retrieval]
    Risk[Risk Review + Approval Routing]
    Brief[Execution Brief Builder]
    Instruction[Agent Instruction Builder]
    Runner[Controlled Coding-Agent Runner]
    CodingAgent[Configured Coding-Agent Subprocess]
    SQLite[(SQLite: Events / Future Runs / Costs)]
    Logs[Logs + Validation Evidence]

    Human --> Telegram
    Human --> Admin
    Caller --> CallerRunner

    Telegram -->|/run ATL-###| Backlog
    Telegram --> Graph
    Backlog --> Graph

    Admin -->|edit local settings| Settings
    CallerRunner -->|validated JSON task| Graph
    Settings --> Brief
    Settings --> Instruction
    Settings --> Runner

    Graph --> Research
    Research --> Risk
    Risk -->|approval needed| Human
    Human -->|/approve or /reject| Telegram
    Risk --> Brief
    Brief --> Instruction
    Instruction --> Runner
    Runner -->|allocate task worktree| TaskWorktree[Request-owned task worktree/branch]
    TaskWorktree -->|shell=False, timeout, captured output| CodingAgent
    CodingAgent --> Runner
    Runner -->|validated branch push| TaskBranch[origin/task branch]
    TaskBranch -->|explicit approval only| Main[origin/main]
    Runner --> Graph

    Graph --> Logs
    Runner --> Logs
    Graph --> SQLite
```

## Implemented Graphs

The codebase currently implements two LangGraph workflows:

1. `src/ai_tech_lead/coding_workflow_graph.py`
   - Exported Studio graph: `graph = build_graph()`
   - Purpose: turn an explicit human request into a bounded coding-agent execution. See `GRAPH_WORKFLOW.md` for the node-by-node route and interrupt/resume flow.
   - Main responsibilities: research gating, risk review, clarification handling, planning, approval routing, request-owned Git worktree execution, validated task-branch push, and explicit main integration.
   - Studio registration: this is the only graph currently listed in `langgraph.json`.
   - Local PNG export: [graph_diagram.png](diagrams/graph_diagram.png)

2. `src/ai_tech_lead/telegram_agent_graph.py`
   - Exported entrypoint: `build_telegram_agent_graph(...)`
   - Purpose: provide a read-only Telegram agent for backlog Q&A and safe tool use.
   - Main responsibilities: chat-thread message handling, tool binding, backlog read-only tools, and reply generation.
   - Studio registration: not exported in `langgraph.json`; it is used by the Telegram operator at runtime.
   - No standalone PNG export: the compiled graph is only the tiny assistant/tools loop, which is accurate but not useful as a Telegram runtime workflow diagram.

The helper `src/ai_tech_lead/graph_diagrams.py` refreshes the main coding workflow PNG in `docs/diagrams/` so the implemented runtime path stays visible without needing to hunt for the rendering code.

Supporting modules such as `src/ai_tech_lead/backlog_graph_runner.py` call the coding workflow graph builder, but they are runners/adapters rather than separate graph implementations.

## Boundaries

- Input adapters turn external messages into explicit local tasks.
  Current adapters: Telegram long polling, the local admin UI, and the JSON subprocess.
  The CLI starts the process, but it does not select backlog tasks.
  Telegram is the human-facing bot for AI Tech Lead: use it directly when you want to talk to the coding agent from chat.
  It must call the bounded workflow and must not become a second agent brain.
  The JSON subprocess enters through `run-agent-task`, which validates the JSON contract before handing the task to the graph.
  Caller-supplied project/context data is normalized there into AI Tech Lead's own immutable internal `TargetProjectContext` before any graph node, planner, or coding-agent step uses it.
  An external caller may own caller-side orchestration, paused-run bookkeeping, and resume or approval UX.
  AI Tech Lead owns the specialist-internal graph, prompts, approvals, and the mapping from internal pauses to the subprocess status contract.
  Future adapters may include web or other chat surfaces.
- Google Sheets is the only canonical backlog. `SheetsBacklogRepository` (`backlog_sheets_repository.py`) is the live repository boundary — Telegram, chat tools, and the Agent Hub subprocess path all read/write through it. `MarkdownBacklogRepository` remains for the historical archive format only and is not constructed by any live call site.
- `data/backlog.sqlite3` holds runtime state only, not backlog ownership: task snapshots (the exact source row and its hash at fetch time), a pending-update outbox for Sheet writes, and sync-conflict records (`backlog_runtime_store.py`). It exposes no create/edit/list/planning operations.
- Every backlog-driven run follows one flow: fetch the exact Sheet row by `BacklogReference` (`backlog_reference.py` — project key, spreadsheet ID, sheet name, item ID; never hardcoded), snapshot it, execute, then write-ahead the completion update to the outbox and attempt an immediate flush. If the source row changed since the snapshot, the update is abandoned and a conflict is recorded instead of overwriting a human edit. If the Sheets call fails, the row stays pending for bounded recovery (`backlog-sync-recover` CLI command, also run automatically at Telegram-operator startup) — retries are bounded per row, never infinite, and a row is never silently reported as synced.
- Only the `Status` and `Evidence / Validation` columns are ever written by runtime code.
- Backlog loading parses the fetched item into graph state. It must not silently choose work.
- Worker coding agents must not freely edit backlog storage. Any backlog edit must be explicitly requested, field-bounded, and owned by the human/orchestrator through a controlled repository boundary.
- The coding workflow graph owns orchestration state, approval routing, brief creation, agent-instruction creation, orchestrator-input request state, and the coding-agent execution node.
- `git_lifecycle.py` is the single Git side-effect boundary for coding runs. It allocates a request-owned worktree from `origin/main`, tracks the exact task branch/base/tip/commit, pushes only after completion verification, and exposes a separate integration approval interrupt. Integration fetches current `origin/main`, reconciles with a normal merge, stops on conflicts or ambiguous canonical ownership, validates the integrated result, pushes `main` without force, fetches again, and verifies task-commit ancestry. Task worktrees are never deleted automatically.
- ATL-039: completion is decided from the AI Tech Lead's own evidence, not the coding agent's success prose. Before the handoff, `5e_discover_validation_command` (`validation_command_discovery.py`) finds the target project's canonical validation command — first from the project's own entry points (`AGENTS.md`, `docs/INDEX.md` one-hop refs, `.agents/skills` best skill: a short line naming the intent that carries exactly one backtick-quoted command), otherwise from exactly one unambiguous repo signal (pytest / `npm test` / `make test` / tox / nox); zero or conflicting signals yield nothing, and the run then pauses at `5e1_validation_command_interrupt` to ask the operator for the command (one bounded retry; if they don't supply one it proceeds and the completion gate escalates to human verification). After the agent reports success, `6b1_capture_validation_evidence` (`completion_evidence.py`) runs in the same checkout the agent used (the request-owned worktree when the Git lifecycle is active, else the resolved project root), takes the changed-file list and a summary straight from `git diff` against the task base — never the agent's self-reported file list — runs the canonical command itself (`shell=False`), and flags changed files unrelated to the approved task. `completion_verifier.py` then resolves to exactly one of four outcomes: **Human verification required** when ATL had no command or could not run one (`validation_passed=None`, no LLM call), or when a correction it asked for is still unmet after one automatic cycle; **Correction required** when ATL's run failed or the LLM names one unmet requirement; **Complete** when the verifier LLM — weighing the real diff, validation output and out-of-scope list against the task/plan/acceptance criteria — is satisfied; **Failed** only when a human rejects the work at the verification interrupt. The coding agent still runs the command for its own feedback, but its report is context only.
- Coding-agent handoffs now have three explicit instruction layers:
  1. AI Tech Lead runtime core, which is reusable across projects.
  2. Reusable runtime skills that travel with the AI Tech Lead runtime.
  3. Target-project rules and target-project skills loaded from the selected `project_root`.
- Tech Lead Analysis reads layer 3's same project-pack files (`AGENTS.md`, `docs/INDEX.md`, `.agents/skills/INDEX.md`) earlier and more loosely than the coding-agent handoff does: bounded, read-only, and optional (`project_guidance_discovery.py`) — a missing file is never an error there, unlike the hard-required extraction `instruction_assembler.py` performs once a coding-agent handoff is actually being built. A governance check (`project_guidance_governance.py`) then decides whether anything discovered — or its absence — actually matters enough to require a human's explicit approval before use; see `GRAPH_WORKFLOW.md`'s `1d_check_project_guidance` / `1d1_project_guidance_interrupt` for the exact rule. Nothing this check proposes is ever written back to the target project's own files automatically — approval only permits using the proposed content for that one run.
- ATL-078: that one selected set of guidance notes is the only one for the whole run — it is threaded (never re-selected as a separate act) through plan generation, plan review, the coding-agent handoff (as an additional section alongside, not instead of, `instruction_assembler.py`'s own extraction), and completion review, and its content hash is compared on approval-resume so a run paused long enough for the target project's own guidance to change re-plans and re-asks for approval instead of silently proceeding. See `GRAPH_WORKFLOW.md`'s ATL-078 bullet for the full per-node breakdown.
- Graph state keeps any conditional orchestrator-input metadata bounded and explicit; see `CONTEXT_MANAGEMENT.md` for the field shape.
- Project-aware steps must read the same validated internal target-project context for research code scans, planning, approval Q&A, coding-agent execution, changed-file checks, completion verification, and backlog attribution rather than independently guessing or defaulting a repo.
- The current coding-agent backend is Codex CLI, but the architecture must stay
  backend-neutral. Future backends may include Claude Code, OpenAI tools, local
  agents, or other compatible execution backends.
- `config/model_registry.json` (`model_registry.py`) is the single authoritative source of
  supported LLM models: endpoint/capability support, reasoning-effort controls, and
  pricing (ATL-035). `execution_profiles.py` is the one resolver boundary between that
  registry and workflow call sites: every orchestrator-LLM call site passes a purpose
  string (e.g. `"risk_review"`), never a raw model ID or reasoning-effort string.
  `PURPOSE_TIER` maps each purpose to a `"simple"`/`"normal"`/`"strong"` tier; each tier has
  a hard reasoning-effort ceiling (low/medium/high) enforced independently of whatever the
  resolved profile or model's own registry default says, and `"xhigh"`/`"max"` are never
  selected automatically. `"normal"` mirrors `settings.orchestrator_ai_model` so config-driven
  model choice keeps working; `"simple"`/`"strong"` are fixed profile definitions — moving a
  tier to a different model is a one-line change there, not workflow-code surgery (ATL-036).
  `profile_override_store.py` layers a SQLite-backed, bounded, expiring per-tier override on
  top of the resolver (`/profile`, `/profile_override`, `/profile_clear` in Telegram) — the
  saved default is never rewritten by an override. `execution_profiles.PURPOSE_PROFILE_OVERRIDE`
  is the analogous per-*purpose* mechanism (ATL-090): individual purposes inside a tier can
  diverge from that tier's default model when their own live benchmark supports it — never a
  tier-wide swap on partial evidence — so `/profile` groups its status output by resolved
  (model, effort) rather than assuming one profile per tier. `llm_json.py`'s JSON-retry path can escalate
  reasoning effort one step within a tier's own ceiling on an invalid response (never crossing
  into another tier); `main.py` validates every static profile against its tier's ceiling at
  startup and fails closed rather than starting with an invalid one. `run_budget.py` provides the
  coding workflow's per-run call/token/cost guardrail. The configured ceilings are snapshotted into
  `GraphState` at the runtime boundary and the accumulated call/input-token/output-token/cost counters
  are checkpointed after every orchestrator-calling node, so Telegram interrupt/resume cannot reset
  usage. The low-level Responses API boundary meters every real provider attempt, including JSON
  retries, incomplete responses and hosted web-search calls. The provider-call ceiling is exact
  before a request is sent; token and estimated-cost ceilings use provider-reported usage, so the
  response that reaches/crosses one of those metered ceilings is retained but no later provider
  request is allowed. Budget limits are never increased automatically. A non-interactive subprocess
  run fails closed with structured usage; Telegram pauses at `run_budget` and can retry the blocked
  step only after the operator explicitly changes settings and approves the retry.
  **Current model assignment** (as of ATL-090, 2026-08-19): `gpt-4.1-mini` remains the base model
  — it backs the entire `"strong"` tier (`tech_lead_analysis`, `plan_review`, unbenchmarked) and
  is `"normal"` tier's settings-driven fallback. `gpt-5.6-luna` was adopted only where a live
  benchmark supported it: all of `"simple"` tier (ATL-036), plus four `"normal"`-tier purposes via
  `PURPOSE_PROFILE_OVERRIDE` — `research_knowledge_gap_check`, `operator_question`,
  `research_discovery`, and `telegram_chat` (the last pinned to `"none"` as a hard requirement,
  not just cost: Luna's `"low"`/`"medium"` 400s over Chat Completions with bound tools). Two
  `"normal"`-tier purposes were benchmarked and deliberately kept on `gpt-4.1-mini` —
  `completion_verification` (Luna produced invalid JSON on the final done-gate) and
  `backlog_draft_builder` (Luna's per-token discount was outweighed by needing more tokens for its
  large structured output). Raw benchmark data lives in the ATL-036/ATL-090 backlog rows, not here.
- Settings own configurable values, limits, and paths, plus the active model for the
  `"normal"` profile and the run-budget ceilings. Human-readable prompt and rule text lives
  in `data/prompts.json`. Prompt
  keys are centralized in `src/ai_tech_lead/prompt_loader.py`, and graph nodes
  or instruction builders load the registry entries on demand when they need
  them. The local admin UI exposes the same registry so prompts can be
  inspected and edited in one obvious place. Research source indexes, approved
  online docs URLs, and fetch limits are also settings-owned so the research
  gate stays bounded and configurable.
- The coding-agent runner owns subprocess execution. It receives a complete
  instruction, project root, and validated settings, then captures stdout and
  stderr without using `shell=True`.
- External callers should call `uv run python -m ai_tech_lead manifest`, cache the
  returned JSON by `manifest_hash`, and refresh only when the hash changes. If the
  manifest includes `manifest_cache_ttl_seconds`, callers may use that as the
  freshness window for re-fetching the full manifest.
- `run-agent-task` uses the same durable, SQLite-backed LangGraph checkpointer the
  Telegram operator uses (`checkpointer_store.py`), keyed by `subprocess-<request_id>`.
  A paused conversation survives across separate subprocess invocations, so a caller
  resumes the exact paused run rather than restarting the task from scratch.
- Each coding run also writes one compact `run_audit_summaries` receipt to the existing
  local runtime SQLite database. It records bounded task, model, approval, Git,
  selected-skill-version, result, validation, and available usage metadata. It does
  not duplicate checkpoint state, raw prompts, provider traces, or unbounded logs.
- Git completion is reported in two distinct states: a validated/pushed task branch
  is `MAIN STATUS: NOT IN MAIN — pushed branch <branch>`, while only ancestry-verified
  integration is `MAIN STATUS: IN MAIN — verified on origin/main at <sha>`. Main
  integration is not deployment.
- LangChain/LangGraph tools, when added, are executable capabilities exposed to an LLM or graph. They are different from this repository's `.agents/skills`, which are reusable coding-agent instructions.
- Admin UI is optional local tooling for editing settings. Browser code keeps HTML, CSS, and JavaScript separated. HTML owns structure, CSS owns presentation, and JavaScript owns behaviour. Browser JavaScript uses ES modules.
- UI field schemas, API paths, labels, and other UI configuration should move to JSON or API-provided configuration when they become shared, large, or reused. Small local constants are acceptable only when they are explicit and easy to replace.
- Placeholder/demo adapters should be removed once a real adapter replaces them, unless the human explicitly wants to keep them for teaching or tests.

## JSON Subprocess Contract

This section replaces the standalone `ARMY_INTEGRATION.md` page. Keep the bounded subprocess contract here so the non-interactive caller boundary has one canonical home.

- A caller invokes AI Tech Lead through `run-agent-task`.
- A caller discovers the callable contract through `uv run python -m ai_tech_lead manifest`.
- A new task is JSON with a task string plus bounded metadata such as `request_id`, optional Hub `run_id`, `source`, explicit `project_root`, `project_reference`, optional `task_kind`, `references`, and `execution_mode`.
- `run-agent-task` accepts generic caller project/context data, validates it once, and converts it into AI Tech Lead's internal immutable `TargetProjectContext`. A supplied target project root must match the authorised `project_registry` or arrive with explicit human approval for that task. Project-aware steps fail clearly instead of silently substituting AI Tech Lead's own repo, and they stop safely when the registered platform, credentials, or location are unavailable.
- `task_kind` defaults to `coding_task`. `task_kind: "backlog_refinement"` routes through the existing backlog-refinement service instead of the coding workflow and is the primary Hub entrypoint for proposing new backlog items.
- Backlog refinement reads project-specific backlog settings from the supplied target-project context (`project_reference.backlog` / `project_reference.backlog_project`) rather than from hardcoded Sheet IDs, tabs, prefixes, columns, or repo-local aliases. Telegram `/propose` reuses the same underlying capability.
- `execution_mode` is a request, not a grant. `instruction_only` is the safe default.
- A paused conversation is resumed by resubmitting the same `request_id` with a `decision: {"option": ..., "text": ..., "actor": ...}` object — no `task` field needed. The option must be one of the names the paused response's `pending_decision.options` just reported; anything else is rejected.
- Output is structured JSON with status, summary, formulated task, brief, coding-agent instruction, backend used, execution flag, a `validation` field (the AI Tech Lead's own authoritative run — `"<command> — passed|failed (AI Tech Lead ran it)"` — falling back to the coding-agent-declared line, normalized adapter-neutrally per the shared `CodingAgentReport` contract, only when ATL had no command or could not run one; `null` when neither exists), logs, evidence, next action, a `pending_decision` block describing exactly what's paused and what decisions are valid right now, and a tiny `agent_manifest` reference.
- When a caller supplies `run_id`, AI Tech Lead also emits versioned `SpecialistProgressEvent` JSONL on stdout while it runs. Normal/debug logs stay on stderr, and the final structured result remains in the caller-provided output JSON file.
- Progress events contain `schema_version`, `run_id`, `request_id`, monotonic `sequence`, `event_type`, stable `phase`, bounded `human_summary`, `occurred_at`, and allowlisted bounded metadata.
- Progress is operational telemetry only. It must not expose chain-of-thought, raw plan text, full prompts, secrets, raw coding-agent/provider output, or unbounded logs.
- Existing LangGraph/coding-runner callbacks are translated into stable external phases. Future Deep Agent custom/task events must pass through the same translation boundary instead of leaking framework-specific events to Hub.
- Deterministic specialist heartbeats may be emitted after a genuinely quiet interval without additional LLM calls. Hub remains responsible for persistence, stale detection, Telegram rate limiting, and operator-facing message updates.
- If `run_id` is omitted, live progress is disabled and stdout remains unused by this contract.
- Supported subprocess statuses are:
  - `success` - work completed or an instruction package is ready.
  - `needs_clarification` - the workflow ended (not paused) needing more information, e.g. an unresolved reference. There is no conversation to resume; the caller should submit a brand new task with the answer folded in.
  - `waiting_decision` - the workflow is genuinely paused (approval, plan/failure guidance, research approval, backlog-refinement approval). See `pending_decision` for the exact options; resume by resubmitting `request_id` with a `decision`.
  - `failed` - terminal failure; the caller should report the error instead of waiting for resume.
- Callers should prefer machine-readable fields over parsing prose:
  - `result_kind` distinguishes `instruction_package`, `execution_result`, `backlog_refinement_draft`, `backlog_item_created`, `clarification_request`, `decision_required`, and `terminal_failure`.
  - `pending_decision` (present only on `waiting_decision`) carries `thread_id` (informational), `kind` (which internal pause this is — relay it, don't interpret it), `prompt` (plain-language description), and `options` (the exact, only valid `decision.option` values right now, each optionally flagged `needs_text`). This is intentionally generic: a caller never needs agent-specific knowledge of what option names mean to relay them correctly, and new pause kinds or option sets require no caller code changes.
- A caller should rely on that bounded JSON contract rather than reading the full codebase.
- Telegram is only an interactive operator surface; it does not replace the subprocess contract.
- Shared-state concurrency rules:
  - duplicate concurrent `run-agent-task` calls with the same `request_id` fail loudly
    instead of overlapping silently
  - real coding-agent subprocess execution is locked per `project_root`, so two runs
    cannot execute against the same repo at once even if the caller misroutes work

## Persistence

SQLite is the preferred local persistence layer when the project needs durable task runs, approvals, events, cost/token usage, or operator audit history. Keep SQLite minimal until those tables are genuinely used.

LangGraph checkpoints remain the source of resumable workflow state. The compact run
audit receipt is an inspectable summary only; retrieve one with
`uv run python -m ai_tech_lead run-audit <request_id>`.

Do not add database tables only because a future feature might need them. Add a table when a workflow reads from it, writes to it, or reports from it.

## Test Strategy

Use `pytest` as the default test runner for the core product:

```bash
uv run pytest
```

The default suite should stay fast and should not call real coding agents. Mock
subprocess behavior in unit tests, and validate real coding-agent execution only through
explicit CLI runs.

Playwright belongs in a later browser/E2E layer when the admin UI or a web input
surface becomes important enough to protect.

## Current Telegram Operating Model

Telegram is the human-facing bot for AI Tech Lead and the normal way to talk
to the coding agent from a phone. Normal text goes through a Telegram intent
router first; the router may classify the message, but it must not execute the
configured coding agent or mutate the backlog by itself. Explicit `/run ATL-###`
selects a backlog task. Explicit `/code` first creates a structured backlog draft;
only `/approve` saves that item and starts the bounded coding workflow. `/run`
continues to execute an existing backlog item directly.

Current Telegram constraints:

- Long polling only; no webhooks yet.
- Single active task only.
- Single owning chat per running process.
- No autonomous task selection.
- Approval still gates progression; Telegram must not bypass the graph approval path.

Telegram enable/disable and coding-agent execution enable/disable must be controlled by settings/admin, not by requiring the human to remember CLI flag combinations. Normal app startup should read settings and start the configured operator surfaces. CLI flags are acceptable only for local development, diagnostics, or explicit debug paths such as `--debug`; they must not become the normal way to enable production behaviour.

These are acceptable MVP constraints because they keep the workflow inspectable and reduce concurrency, security, and deployment complexity. Revisit them only when the single-user operator loop is proven useful.

## Growth Rule

Prototype scope means small, not disposable. Add narrow modules around durable
boundaries instead of mixing adapters, graph logic, settings, subprocess calls,
and UI behavior in the same file.

When a prototype file is superseded by a real implementation, either remove it
or clearly document why it still exists. Do not let old placeholders become
silent dead code.
