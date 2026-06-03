# Architecture

This project is a local AI technical lead orchestrator. The core product is not
the admin UI; it is the workflow that turns an explicit human request into a
bounded coding-agent execution.

## Boundaries

- Input adapters turn external messages into explicit local tasks.
  Current adapters: CLI backlog task selection and a local Telegram placeholder.
  Future adapters may include Telegram, web, or other chat surfaces.
- Backlog loading parses local task data and converts one selected item into
  graph state. It must not silently choose work.
- The coding workflow graph owns orchestration state, approval routing, brief
  creation, agent-instruction creation, and the coding-agent execution node.
- Settings own configurable values and prompt templates. Graph nodes render
  templates from settings instead of duplicating prompt text.
- The coding-agent runner owns subprocess execution. It receives a complete
  instruction, project root, and validated settings, then captures stdout and
  stderr without using `shell=True`.
- Admin UI is optional local tooling for editing settings. Browser JavaScript
  uses ES modules.

## Test Strategy

Use `pytest` as the default test runner for the core product:

```bash
uv run pytest
```

The default suite should stay fast and should not call real coding agents. Mock
subprocess behavior in unit tests, and validate real Codex execution only through
explicit CLI runs.

Playwright belongs in a later browser/E2E layer when the admin UI or a web input
surface becomes important enough to protect.

## Growth Rule

Prototype scope means small, not disposable. Add narrow modules around durable
boundaries instead of mixing adapters, graph logic, settings, subprocess calls,
and UI behavior in the same file.
