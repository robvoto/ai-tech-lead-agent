# AI Tech Lead Agent

AI Tech Lead is a local specialist agent that turns approved technical work into bounded, reviewable coding execution. External callers submit validated tasks through the JSON subprocess adapter; AI Tech Lead then prepares a plan, invokes the configured coding-agent CLI through its controlled runner, validates the result, and reports evidence rather than assuming success.

## Platform role

```text
Human request or backlog item
            │
            ▼
        Agent Hub
            │
            ▼
     AI Tech Lead Agent
            │
            ├── resolve pinned project context
            ├── inspect bounded repository evidence
            ├── prepare and refine an execution plan
            ├── invoke the configured coding agent
            └── validate changes, tests, and outcome
```

AI Tech Lead is a specialist. Agent Hub owns orchestration and task state; Agent Factory owns agent creation and lifecycle management.

## Responsibilities

- accept validated caller tasks through the inbound JSON subprocess contract;
- interpret a technical task within its pinned project context;
- inspect relevant code, documentation, skills, and constraints;
- prepare a bounded coding plan;
- pause for clarification or approval when required;
- invoke the configured coding-agent CLI through the controlled runner;
- constrain file, command, project, and execution scope;
- review changed files and validation evidence;
- report completed, partial, blocked, or failed outcomes accurately;
- retain reusable technical knowledge without silently changing project rules.

## Repository boundaries

| Repository | Responsibility |
|---|---|
| `agent-hub` | Orchestration, routing, task state, approvals, and operator interaction |
| `agent-factory` | Agent creation, validation, staging, approval, and promotion |
| `ai-tech-lead-agent` | Technical planning and bounded coding-agent coordination |

AI Tech Lead must not become a general orchestrator, silently select a different project, or treat coding-agent output as validated work.

## Execution workflow

```text
Task accepted
     ▼
Project context pinned
     ▼
Relevant evidence inspected
     ▼
Plan prepared and reviewed
     ▼
Approval or clarification when required
     ▼
Coding agent invoked
     ▼
Diff, tests, lint, and task outcome checked
     ▼
Evidence-based result returned
```

## Current capabilities

- Telegram operator interaction and non-interactive CLI/process commands;
- backlog-task execution;
- machine-readable inbound JSON subprocess input and structured output;
- bounded coding-agent CLI invocation through a controlled runner;
- approval and clarification interruptions;
- configurable execution enablement through local settings;
- project-aware repository inspection;
- test, lint, and changed-file validation;
- local administration interface;
- LangGraph development and inspection workflow;
- persistent knowledge-store diagnostics;
- setup and health checks.

The CLI starts services and exposes non-interactive maintenance or adapter commands. It is not a separate interactive task-submission interface.

## Repository structure

```text
.
├── src/ai_tech_lead/          # Workflow, contracts, tools, and interfaces
├── config/                    # Local-safe defaults and project metadata
├── docs/                      # Architecture, workflow, runtime, and planning guidance
├── tests/                     # Automated workflow and contract tests
└── scripts and run helpers    # Local development entry points
```

## Local development

Install dependencies and run the fast validation suite using the commands documented in the runtime guide. Core checks include:

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

The default test suite should not call a real coding agent unless an explicit integration test enables that behaviour.

## Architecture principles

- **Pinned project context** — execution cannot silently move to another repository.
- **Plan before modification** — the coding agent receives a bounded task rather than an ambiguous request.
- **Approval for consequential actions** — risky execution pauses rather than proceeding silently.
- **Untrusted agent output** — diffs and claimed success require validation.
- **Evidence-based completion** — a task is complete only when the requested outcome and relevant checks are demonstrated.
- **Controlled execution** — coding-agent execution is disabled unless explicitly enabled in local settings.
- **No hidden scope expansion** — extra files, commands, tools, or research require a justified task need and the applicable approval.

## Documentation

Start with [`docs/INDEX.md`](docs/INDEX.md).

Key references:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/GRAPH_WORKFLOW.md`](docs/GRAPH_WORKFLOW.md)
- [`docs/RUNTIME_RUNBOOK.md`](docs/RUNTIME_RUNBOOK.md)
- [`docs/plan/INITIAL_PLAN.md`](docs/plan/INITIAL_PLAN.md)

Operational paths, local bot commands, Studio commands, and environment-specific run instructions belong in the runtime documentation rather than the repository landing page.

## Security

See [`SECURITY.md`](SECURITY.md) for coding-agent, command, repository, Telegram, credential, and validation boundaries.

## Licence

This private repository does not grant an open-source licence. A licence should be selected deliberately before any public source release.
