# Architecture

This project is a local AI technical lead orchestrator. The core product is not
the admin UI or Telegram surface; it is the workflow that turns an explicit
human request into a bounded coding-agent execution.

## Design Diagram

This diagram is maintained as Mermaid text so it can evolve with the code. It is not a one-off drawing.

```mermaid
flowchart TD
    Human[Human Operator]
    Telegram[Telegram Long Polling Adapter]
    Admin[Local Admin UI]
    Army[Agent Army]
    ArmyRunner[JSON subprocess adapter<br/>run-agent-task]

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
    Army --> ArmyRunner

    Telegram -->|/run JH-###| Backlog
    Telegram -->| Graph
    Backlog --> Graph

    Admin -->|edit local settings| Settings
    ArmyRunner -->|validated JSON task| Graph
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
    Runner -->|shell=False, timeout, captured output| CodingAgent
    CodingAgent --> Runner
    Runner --> Graph

    Graph --> Logs
    Runner --> Logs
    Graph --> SQLite
```

## Implemented Graphs

The codebase currently implements two LangGraph workflows:

1. `src/ai_tech_lead/coding_workflow_graph.py`
   - Exported Studio graph: `graph = build_graph()`
   - Purpose: turn an explicit human request into a bounded coding-agent execution. See `GRAPH_WORKFLOW.md` for the node-by-node route.
   - Main responsibilities: research gating, risk review, clarification handling, planning, approval routing, agent-instruction creation, and controlled coding-agent execution.
   - Studio registration: this is the only graph currently listed in `langgraph.json`.
   - Local PNG export: `data/graph_diagram.png`

2. `src/ai_tech_lead/telegram_agent_graph.py`
   - Exported entrypoint: `build_telegram_agent_graph(...)`
   - Purpose: provide a read-only Telegram agent for backlog Q&A and safe tool use.
   - Main responsibilities: chat-thread message handling, tool binding, backlog read-only tools, and reply generation.
   - Studio registration: not exported in `langgraph.json`; it is used by the Telegram operator at runtime.
   - Local PNG export: `data/telegram_agent_graph.png`

The helper `src/ai_tech_lead/graph_diagrams.py` refreshes both PNGs in `data/` so the diagrams stay visible without needing to hunt for the rendering code.

Supporting modules such as `src/ai_tech_lead/backlog_graph_runner.py` call the coding workflow graph builder, but they are runners/adapters rather than separate graph implementations.

## Boundaries

- Input adapters turn external messages into explicit local tasks.
  Current adapters: Telegram long polling, the local admin UI, and the Agent Army JSON subprocess.
  The CLI starts the process, but it does not select backlog tasks.
  Telegram is an operator surface only; it must call the bounded workflow and must not become a second agent brain.
  Agent Army enters through `run-agent-task`, which validates the JSON contract before handing the task to the graph.
  Future adapters may include web or other chat surfaces.
- Backlog storage must stay behind a repository boundary. The current working backlog file is `data/backlog/ai_tech_lead_backlog.xlsx`; the archived Markdown source/backup lives at `data/backlog/archive/BACKLOG.md`.
- Backlog loading parses local task data and converts one selected item into
  graph state. It must not silently choose work.
- Worker coding agents must not freely edit the Excel backlog. Any backlog edit must be explicitly requested, field-bounded, and owned by the human/orchestrator until a controlled backlog repository handles spreadsheet writes safely.
- The coding workflow graph owns orchestration state, approval routing, brief creation, agent-instruction creation, orchestrator-input request state, and the coding-agent execution node.
- Graph state keeps any conditional orchestrator-input metadata bounded and explicit; see `CONTEXT_MANAGEMENT.md` for the field shape.
- The current coding-agent backend is Codex CLI, but the architecture must stay
  backend-neutral. Future backends may include Claude Code, OpenAI tools, local
  agents, or other compatible execution backends.
- Settings own configurable values, limits, paths, model names, and prices.
  Human-readable prompt and rule text lives in `data/prompts.json`. Prompt
  keys are centralized in `src/ai_tech_lead/prompt_loader.py`, and graph nodes
  or instruction builders load the registry entries on demand when they need
  them. The local admin UI exposes the same registry so prompts can be
  inspected and edited in one obvious place. Research source indexes, approved
  online docs URLs, and fetch limits are also settings-owned so the research
  gate stays bounded and configurable.
- The coding-agent runner owns subprocess execution. It receives a complete
  instruction, project root, and validated settings, then captures stdout and
  stderr without using `shell=True`.
- LangChain/LangGraph tools, when added, are executable capabilities exposed to an LLM or graph. They are different from this repository's `.skills`, which are reusable coding-agent instructions.
- Admin UI is optional local tooling for editing settings. Browser code keeps HTML, CSS, and JavaScript separated. HTML owns structure, CSS owns presentation, and JavaScript owns behaviour. Browser JavaScript uses ES modules.
- UI field schemas, API paths, labels, and other UI configuration should move to JSON or API-provided configuration when they become shared, large, or reused. Small local constants are acceptable only when they are explicit and easy to replace.
- Placeholder/demo adapters should be removed once a real adapter replaces them, unless the human explicitly wants to keep them for teaching or tests.

## Persistence

SQLite is the preferred local persistence layer when the project needs durable task runs, approvals, events, cost/token usage, or operator audit history. Keep SQLite minimal until those tables are genuinely used.

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

Telegram is the primary operator channel for the prototype and the normal way
to communicate with the app when the human is away from the PC. Normal text
goes through a Telegram intent router first; the router may classify the
message, but it must not execute the configured coding agent or mutate the
backlog by itself. Explicit `/run JH-###` selects a backlog task. Explicit
`/code` starts the bounded coding workflow.

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
