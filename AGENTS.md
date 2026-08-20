# AGENTS.md

Purpose: minimal always-loaded repository instructions for AI agents working in this project.

This file is a routing layer only. It is not the project manual, backlog process, architecture guide, skill catalogue, standards document, or test plan.

## Default workflow

1. Use `docs/INDEX.md` to find the smallest relevant project document.
2. Use `.skills/INDEX.md` to choose one relevant task skill.
3. Inspect the current files before giving code-specific advice or editing.
4. If changing project setup, architecture, runtime behaviour, documentation, backlog, automation, config, tests, environment examples, packaging, templates, AI model/provider defaults, cost logging, approval workflows, long-running workflows, `AGENTS.md`, or skills, read `docs/STANDARDS_INDEX.md` first.
5. Before any branch/worktree, commit, push, PR, merge, or `main`-integration action, use `.skills/git-lifecycle/SKILL.md`.
6. Do not load the whole repository unless the task explicitly requires a broad audit.

## Navigation

- Project docs: `docs/INDEX.md`
- Shared standards pointers: `docs/STANDARDS_INDEX.md`
- Task skills: `.skills/INDEX.md`

## Governed self-improvement

- The agent may improve its own reusable skills or `AGENTS.md` without separate approval when evidence from completed work shows a repeatable problem, recurring correction, avoidable rework, or stable procedure.
- Keep every improvement bounded to the demonstrated problem. Do not broaden purpose, permissions, memory access, tool access, runtime authority, or repository scope.
- Before editing, record the evidence, target file, expected reusable benefit, risk, and validation method in the task trace or final report.
- Reusable skills must remain concise and procedural, be registered in `.skills/INDEX.md`, and be validated with the smallest relevant test or deterministic check.
- Do not duplicate policy across skills and `AGENTS.md`. Put universal behavioural rules in `AGENTS.md`; put task-specific procedures in skills.
- Code or runtime self-modification still requires the normal approved bounded coding workflow and relevant validation.
- Stop without changing anything when the evidence, target, ownership, or validation method is unclear.

## Universal rules

- Never guess or invent.
- Before introducing or relying on heuristic/approximate inference, use the global `heuristic-review` guardrail. Assistive heuristics may help an LLM or reduce search cost when they cannot determine the final outcome; heuristics that decide semantic meaning, business outcome, target, permission, or action require explicit human approval.
- Keep context bounded. Load the smallest file set that can answer the task.
- Keep work bounded and small. Touch only files required for the task.
- Do not add hidden autonomous behaviour, broad discovery loops, or uncontrolled self-improvement.
- Do not add compatibility shims, duplicate implementations, unused code, dead code, or legacy code unless explicitly requested.
- Do not hardcode hidden choices. If a prototype hardcode is explicitly approved, state why, where it lives, and what would make it configurable later.
- Do not add fallback/default behaviour that changes the outcome unless explicitly approved.
- On uncertainty, missing standards, failed validation, unavailable tools, invalid AI output, or ambiguous requirements, stop or escalate instead of silently choosing an alternate path.
- Stop and ask before destructive, broad, risky, ambiguous, expensive, repo-changing, or code-executing actions unless the human has already approved them.
- Runtime safety must be enforced in code/settings/admin, not only in instruction files.
- Do not mask failures with broad fallback logic or silent defaults.
- Do not claim completion without validation evidence or a clear reason validation was not applicable.

## Finish report

Report only what matters when the agent finishes a task:

- Files changed
- Behaviour changed
- Self-improvement evidence and validation, when applicable
- Validation command/result, or why not run
- Remaining risk or follow-up

## Repository text format

- All tracked text files use LF line endings. `.gitattributes` and `.editorconfig` are authoritative; do not introduce or preserve CRLF.
- Before finishing edits, run `git diff --check`. If a touched tracked text file is CRLF or mixed, normalize that touched file to LF without rewriting unrelated dirty work.
