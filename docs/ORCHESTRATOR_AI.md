# Orchestrator AI

## Purpose

The orchestrator AI is used for bounded workflow decisions, starting with risk review. It is not the coding agent and it does not modify files.

## Current Use

The first supported use is task risk review. When enabled, the graph asks the orchestrator AI whether a task needs human approval before the workflow continues.

## Secret Handling

The OpenAI API key is read from `OPENAI_API_KEY` in the local WSL environment or from the local `.env` file. The real key must never be committed.

Use `.env.example` as the template and put the real value only in `.env`.

## Safety Behaviour

If orchestrator AI is disabled, missing a key, fails, times out, returns invalid JSON, or returns low confidence, the workflow falls back to requiring human approval. Telegram should show a short human reason, not the raw internal risk-review text, raw graph state, or duplicated task body.

## Settings

The local settings include:

- `orchestrator_ai_enabled`
- `orchestrator_ai_model`
- `orchestrator_ai_max_output_tokens`
- `orchestrator_ai_timeout_seconds`

These settings are also exposed in the admin UI.

The current default model is `gpt-4.1-mini`. Keep the model configurable through settings/admin rather than hardcoding it into the risk-review code.

## Reuse Rule

When adding AI provider configuration, settings UI, token/cost handling, or similar orchestration behaviour, inspect the Job Hunter project first and reuse clean applicable patterns instead of reinventing them. Adapt patterns only when they fit this project boundary.

## Boundary

The orchestrator AI can recommend approval state only. It must not bypass human approval, choose work silently, or enable coding-agent execution.

## Runtime Logging

Workflow logs should clearly separate each LangGraph node and decision. Use visible separators and labels such as `NODE` or `DECISION` so the human can learn the graph flow while watching the terminal.

Risk-review logs should show the AI channel and model. Coding-agent logs should show whether execution is enabled, the command name, and whether token/cost tracking is available.
