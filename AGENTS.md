# AGENTS.md

Purpose: minimal always-loaded repository instructions for AI agents working in this project.

This file is a routing layer only. It is not the project manual, backlog process, architecture guide, skill catalogue, or test plan.

## Default workflow

1. Use `docs/INDEX.md` to find the smallest relevant project document.
2. Use `.skills/INDEX.md` to choose one relevant task skill.
3. Inspect the current files before giving code-specific advice or editing.
4. Do not load the whole repository unless the task explicitly requires a broad audit.

## Navigation

- Project docs: `docs/INDEX.md`
- Task skills: `.skills/INDEX.md`

## Universal rules

- Never guess or invent.
- Keep context bounded. Load the smallest file set that can answer the task.
- Keep work bounded and small. Touch only files required for the task.
- Do not add hidden autonomous behaviour, broad discovery loops, or silent self-improvement.
- Do not add compatibility shims, duplicate implementations, unused code, or dead code unless explicitly requested.
- Do not hardcode hidden choices. If a prototype hardcode is intentional, state why and where it should become configurable later.
- Do not leave unused or legacy code.
- Stop and ask before destructive, broad, risky, ambiguous, expensive, or code-executing actions unless the human has already approved them.
- Runtime safety must be enforced in code/settings/admin, not only in instruction files.
- Do not mask failures with broad fallback logic or silent defaults.
- Do not claim completion without validation evidence or a clear reason validation was not applicable.

## Finish report

Report only what matters:

- Files changed
- Behaviour changed
- Validation command/result, or why not run
- Remaining risk or follow-up
