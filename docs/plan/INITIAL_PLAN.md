# AI Technical Lead Agent - Project Plan and Notes

## Planning hook

This file captures the current project plan and setup context:

```text
E:\Programming\ai-tech-lead\docs\plan\INITIAL_PLAN.md
```

Future project sessions should read this file before proposing architecture, setup, or implementation steps.

## Project identity

Build a local AI Technical Lead Assistant.

The assistant should supervise coding tools rather than replace them.

Primary goal:
- learning modern AI orchestration
- controlling token/cost usage
- building enterprise-relevant AI workflow experience
- creating a demonstrable portfolio project

The system should prevent common coding-agent failure modes:
- doing more than requested
- making risky changes without approval
- adding unapproved fallbacks
- preserving useless legacy code
- leaving unused code
- hardcoding sample heuristics as general rules
- skipping useful help text/docstrings
- claiming completion without evidence

## Current project root

Windows path:

```text
E:\Programming\ai-tech-lead
```

WSL runtime path:

```text
/mnt/e/Programming/ai-tech-lead
```

Correct runtime terminal:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

Wrong runtime terminal:

```text
PS E:\Programming\ai-tech-lead>
```

Do not use PowerShell as the project runtime terminal.

## Current preferred stack

- Python 3.13
- WSL2 Ubuntu runtime
- Windows VS Code opened in WSL mode
- uv
- LangGraph ecosystem
- LangGraph Studio for visual/debug runs
- SQLite
- Later: Telegram Bot API, LiteLLM, Deep Agents, and provider-neutral coding-agent routing

## Normal project commands

From WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead
```

Check project Python:

```bash
uv run python --version
```

Expected:

```text
Python 3.13.x
```

Validate LangGraph import:

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

Expected:

```text
LangGraph OK
```

Use `uv run ...` for project commands. Do not rely on plain `python3`, because that shows Ubuntu system Python.

## Local admin app

Run the local admin screen from WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead
```

Current default admin URL:

```text
http://127.0.0.1:8766/
```

Current settings API:

```text
http://127.0.0.1:8766/api/settings
```

The admin screen is a local HTML page served by Python:

```text
src/ai_tech_lead/admin/admin.html
src/ai_tech_lead/admin/admin.js
src/ai_tech_lead/admin/admin_form.js
src/ai_tech_lead/admin/admin_config.js
src/ai_tech_lead/admin_server.py
```

The JavaScript is intentionally separate from the HTML and uses ES modules.

The admin screen edits validated local settings and the prompt registry through
the API. The current coding-agent settings file is:

```text
data/coding_agent_settings.json
```

Python validates settings before saving them. The JSON contains adjustable
prototype settings for values the app currently consumes, including runtime,
Telegram, and admin settings. Prompt text now lives in `data/prompts.json` so
the registry stays visible in one place without being mixed into settings.

Agent policy, directory allowlists, model routing, and tool permissions are now
exposed in admin as validated local settings. Keep new fields explicit and
small, and do not hide product behaviour behind undocumented local state.

## VS Code / WSL rule

Open the project from WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
code .
```

Expected VS Code indicator:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

Select interpreter:

```text
/mnt/e/Programming/ai-tech-lead/.venv/bin/python
```

If VS Code shows Windows Python paths, the project is not open in WSL mode.

## Dependency workflow

Use uv.

Do not use raw pip, manual venv activation, or `requirements.txt` as the normal workflow.

Useful commands:

```bash
uv sync --link-mode=copy
uv add package-name
uv add --dev "langgraph-cli[inmem]"
```

Use `--link-mode=copy` because the repo is currently under `/mnt/e`, a Windows-mounted drive.

## LangGraph Studio / local server

Studio is a visual debugging UI. It does not replace VS Code.

Run local server:

```bash
cd /mnt/e/Programming/ai-tech-lead
./run_langsmith.sh
```

Expected local API:

```text
http://127.0.0.1:2024
```

Current `langgraph.json` mapping:

```json
{
  "graphs": {
    "coding_workflow_graph": "ai_tech_lead.coding_workflow_graph:graph"
  }
}
```

Studio should import the exported `graph` object from:

```text
src/ai_tech_lead/coding_workflow_graph.py
```

Important: the exported Studio graph must not pass `MemorySaver()`. LangGraph API / Studio provides persistence.

Use `MemorySaver()` only in the local terminal demo runner.

LangSmith tracing errors such as `403 Forbidden` are separate from the graph logic. For local graph learning, tracing can be disabled with:

```text
LANGSMITH_TRACING=false
LANGCHAIN_TRACING_V2=false
```

## Current graph status

Core graph file:

```text
src/ai_tech_lead/coding_workflow_graph.py
```

Normal CLI runner:

```text
src/ai_tech_lead/backlog_graph_runner.py
```

Backlog loader:

```text
src/ai_tech_lead/backlog_loader.py
```

Coding-agent subprocess runner:

```text
src/ai_tech_lead/coding_agent_runner.py
```

Current graph flow:

```text
read_request
→ check_approval
→ create_brief
→ if approval required: approval_required interrupt → route by approved flag
    → approved=True: create_agent_instruction → run_coding_agent → end_node → END
    → approved=False: end_node → END
→ if approval not required: create_agent_instruction → run_coding_agent → end_node → END
```

Current state fields:

```text
request
brief
needs_approval
approval_reason
approved
agent_instruction
coding_agent_result
```

Current behaviour:
- Reads backlog item by explicit `--task-id`.
- Converts it into graph state.
- Uses explicit backlog approval fields instead of keyword risk heuristics.
- Creates a bounded execution brief.
- Pauses before `approval_required` when the backlog item requires approval.
- Stores the generated coding-agent instruction in `state["agent_instruction"]`.
- Runs `run_coding_agent` node after instruction creation.
- Coding-agent execution is controlled by settings and CLI override, and is currently safe when disabled.
- Stores the coding-agent subprocess summary in `state["coding_agent_result"]`.

## Backlog rule

Current working backlog:

```text
data/backlog/ai_tech_lead_backlog.xlsx
```

Legacy/source backup:

```text
docs/BACKLOG.md
```

The Excel workbook was created from the Markdown backlog because the Markdown structure had drifted: some items had rich metadata, while others were missing fields such as Status, Creator, Epic, Type, Priority, Size, Problem, Outcome, Acceptance Criteria, or Constraints.

Treat the Excel workbook as the working backlog and `docs/BACKLOG.md` as historical/source backup. The code reads and writes the workbook through the backlog repository boundary, so spreadsheet edits are available at runtime while remaining controlled.

Backlog selection must not be hidden.

Normal prototype selection should use explicit ID:

```python
load_backlog_item_by_id("ATL-001")
```

Demo-only selection is allowed only if the function name says so, for example:

```python
load_first_backlog_item_for_demo()
```

Do not silently select the first task in production-like flow.

Worker coding agents must not freely edit `data/backlog/ai_tech_lead_backlog.xlsx`. Spreadsheet edits must be explicit, field-bounded, and human-approved.

## Initial prototype scope

The first useful prototype should include:

1. Read one backlog task by explicit ID.
2. Create a bounded execution brief.
3. Pause for human approval when the backlog item requires approval.
4. Store an agent instruction package in graph state.
5. Run a controlled coding-agent subprocess node.
6. Keep real coding-agent execution disabled unless explicitly enabled.
7. Capture coding-agent stdout/stderr/result back into graph state.
8. Log validation evidence.
9. Add basic token/cost logging later, before real model usage grows.

Not in initial scope:
- RAG
- full autonomous company/agent
- broad multi-agent ecosystem
- full IDE replacement
- unbounded retry loops
- large repo scans without reason

## LangGraph learning notes

### Node name vs function

In LangGraph:

```python
workflow.add_node("approval_required", approval_required_node)
```

means:
- `"approval_required"` is the graph node name.
- `approval_required_node` is the Python function that runs.

A routing function returns the next node name, not a Python function.

### Conditional routing

Use explicit `path_map` for conditional edges so Studio diagrams stay readable.

Preferred project pattern:

```python
workflow.add_conditional_edges(
    NodeName.CREATE_BRIEF,
    route_after_brief,
    {
        NodeName.APPROVAL_REQUIRED: NodeName.APPROVAL_REQUIRED,
        NodeName.EXECUTE_TASK: NodeName.EXECUTE_TASK,
    },
)
```

### StrEnum convention

The project currently uses `StrEnum` for node names once the graph has enough nodes that repeated strings become error-prone.

This is a maintainability choice, not a LangGraph requirement.

### Interrupt style currently used

Current graph uses:

```python
interrupt_before=[NodeName.APPROVAL_REQUIRED]
```

This means:

```text
If the graph is about to enter approval_required, pause first.
```

For this breakpoint style, resume local terminal execution with:

```python
app.invoke(None, config=thread_config)
```

Do not use `app.resume(...)`; that method does not exist.

Do not confuse this with `interrupt(...)` inside a node, which uses a different resume pattern.

### Checkpointer / thread mental model

Checkpoint:
- saved snapshot of graph state and next node.

MemorySaver:
- temporary local checkpoint storage for the terminal demo.

thread_id:
- ID/name of one graph run, so LangGraph knows which paused run to resume.

## Source priority for LangGraph questions

Use sources in this order:
1. Official LangChain / LangGraph / LangSmith docs.
2. Official Python docs.
3. LangGraph GitHub source/docstrings if docs are unclear.
4. LangChain Academy material as learning examples only.
5. Blogs/videos/forums/AI answers only as secondary clues.

Academy material is useful for learning concepts, but it is not the production architecture source of truth.

## Project operating rules

- Inspect current code before giving code-specific advice.
- Do not guess when current code or official docs can be checked.
- Keep changes small and bounded.
- Do not create hidden autonomous behaviour.
- Do not hardcode hidden choices.
- Do not mask failures with broad fallback logic or silent defaults.
- Ask for approval before destructive, broad, risky, ambiguous, or expensive actions.
- Touch only files required for the task.
- Add concise help text/comments when behaviour is not obvious.
- Do not claim done without validation evidence.

## Skills

Current project skills are indexed in:

```text
.skills/INDEX.md
```

Use the index to choose one relevant skill before acting.

Do not copy Job Hunter-specific skills into this project unless they are truly project-agnostic.

## Testing rule

Use risk-based validation:
- For code changes, run the smallest relevant validation command.
- For graph changes, test the affected route.
- For startup/config changes, run app startup.
- For instruction-only changes, check the file structure and avoid code validation unless required.

Record the validation command/result before calling work done.

## Current next step

Next important implementation step:

```text
Inspect and harden the real coding-agent execution path before enabling real coding-agent execution.
```

Purpose:
- Confirm `coding_agent_runner.py` uses `subprocess.run` safely.
- Confirm `shell=False`.
- Confirm execution is disabled by default.
- Confirm real coding-agent execution is controlled by settings/admin, not by a normal CLI flag.
- Confirm the graph records the result in `state["coding_agent_result"]`.
- Add a `restart_required` state flag if the coding agent changes files under `src/ai_tech_lead`.

After that:

```text
Run one small real coding-agent task from a safe backlog item and inspect the graph state/result.
```
