# Context Management

This document defines what context may be shown to the orchestrator, Telegram operator messages, graph logs, and coding-agent handoffs.

## Principle

Keep persisted state raw, bounded, and inspectable. Format prompts and user-facing messages only at the point where they are needed.

## Graph state

Graph state should store structured/raw facts:

- user request
- selected backlog item ID
- risk-review result
- research evidence source titles, locations, and summaries
- approval status
- conditional orchestrator-input request state
- selected route
- coding-agent handoff summary
- execution result metadata

Graph state should not store large preformatted prompts, unbounded logs, secrets, tokens, or duplicated message bodies.

## Conditional orchestrator input

The workflow may track a pending orchestrator-input request in state when a
node needs clarification, risk review, or confirmation later in the flow. Keep
that state bounded and explicit, for example:

- whether orchestrator input is required
- the kind of input needed
- the reason for the request
- the exact question to ask
- which node requested it
- any short task feedback already captured

Use this state for future interrupt/resume handling, not as a default pause signal. Low-risk work should continue until the graph logic decides otherwise.

## Prompt assembly

Prompts should be assembled close to the node that uses them, and each node
should receive only the context it needs for that step. Avoid injecting full
backlog files, full logs, full raw graph state, or unrelated docs into a
prompt.

Any new context fragment over roughly 1,000 tokens needs an explicit reason in code comments, docs, or the finish report.

## Telegram messages

Telegram is an operator surface, not a debug console.

Telegram may show:

- short status
- requested action
- compact approval reason
- next command options
- success/failure summary

Telegram must not show:

- raw graph state
- raw prompts
- secrets or tokens
- internal approval payloads
- duplicated full request bodies
- long diagnostic dumps

Detailed reasons belong in state/logs/admin surfaces, not phone-first Telegram messages.

## Specialist progress events

Hub-facing progress is a phone-first operational summary, not a reasoning or log channel.

Progress may contain:

- stable phase name
- short human-safe milestone
- elapsed seconds, line count, attempt number, or other allowlisted bounded metrics
- waiting, warning, failure, or completion state

Progress must not contain:

- chain-of-thought or hidden reasoning
- full prompts or plan bodies
- raw coding-agent or provider output
- secrets, tokens, environment values, or unrestricted paths
- unbounded logs or arbitrary metadata

The specialist translates internal LangGraph, Deep Agent, and coding-runner events into the same external contract. Hub owns persistence, stale detection, rate limiting, and Telegram presentation.

## Coding-agent handoffs

Coding-agent handoffs should be bounded and reviewable.

A handoff may include:

- project purpose
- project context (documentation pointers, key source paths — supplied by `project_context` in settings)
- relevant backlog item
- specific files to inspect/change
- constraints
- validation command
- safety/approval status
- bounded research evidence from local docs or approved online docs

A handoff should not include:

- full repository dumps
- unrelated backlog items
- full logs
- raw Telegram conversations beyond the specific user request
- secrets or environment values

## Logs

Logs should help diagnose behaviour without becoming the primary context
source for future prompts.

Use logs for evidence and debugging. Do not feed unbounded logs back into the agent. When logs are needed for an LLM step, summarize or select the smallest relevant extract.

## Persistence/checkpoint review

Human approval flows depend on reliable persisted state. Approval, rejection, resume, stale approval, and failed coding-agent handoff cases should be covered by targeted tests before changing persistence/checkpoint behaviour.
