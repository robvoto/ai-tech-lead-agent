# AI Tech Lead - Contributor Instructions

## Purpose

This file defines how humans and AI coding assistants should work on this repository.

It is not the runtime instruction prompt for the AI Technical Lead Assistant.

Runtime agent instructions will live separately inside the application when needed.

## Startup protocol

Before architecture or implementation work:

1. Read `docs/plan/INITIAL_PLAN.md`.
2. Read this file.
3. Read only the specific files needed for the current step.

Do not scan the whole repository by default.

## Learning-first workflow

This project is for learning as well as building.

For each meaningful change:

1. Explain the concept in plain English.
2. Point to the relevant Academy lesson or project doc when available.
3. Show the small change.
4. Run the smallest useful validation.
5. Explain the result.

## Project direction

Build a local AI Technical Lead Assistant.

The assistant should supervise Codex, Claude Code, and Gemini rather than replace them.

The first version should stay small and controlled.

## Current environment

- Project root: `E:\Programming\ai-tech-lead`
- WSL path: `/mnt/e/Programming/ai-tech-lead`
- VS Code workspace mode: Windows workspace
- Runtime commands: WSL terminal when Linux behaviour is needed
- Python: 3.13
- Package Manager: uv

## Design rules

- Prefer small, single-purpose modules.
- Avoid hardcoded business rules, model names, thresholds, and hidden defaults.
- Do not add broad fallback behaviour without evidence and approval.
- Do not hide errors with broad exception swallowing.
- Add concise module docstrings for Python modules.
- Touch only files needed for the current task.
- Remove dead code instead of preserving unused paths.

## Context discipline

- Do not read full repositories by default.
- Do not read full backlog files by default.
- Use compact indexes or targeted reads where possible.
- Ask before expensive, broad, destructive, or unclear work.

## Testing rule

Run the smallest validation that proves the changed behaviour.

Record the exact command and result before claiming work is done.

## Definition of done

A step is done only when:

1. The change is explained.
2. The relevant files are updated.
3. The smallest useful validation has run.
4. The result is recorded.
5. No unrelated files were changed.
