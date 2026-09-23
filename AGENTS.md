# Agent Instructions

Minimal shared routing instructions for AI Tech Lead. This file is not the project manual, architecture guide, backlog process, standards document, skill catalogue, or test plan.

## Default workflow

1. Use `docs/INDEX.md` to find the smallest relevant project document.
2. Use `.agents/skills/INDEX.md` to choose the smallest relevant task skill.
3. Inspect current files before editing or giving code-specific advice.
4. For changes to project setup, architecture, runtime, documentation, backlog, automation, config, tests, packaging, templates, models/providers, cost logging, approval workflows, `AGENTS.md`, or skills, read `docs/STANDARDS_INDEX.md` first.
5. For branch/worktree, commit, push, PR, merge, or `main` integration, use `.agents/skills/git-lifecycle/SKILL.md`.
6. Do not load the whole repo unless the task requires a broad audit.

## Navigation

- Project docs: `docs/INDEX.md`
- Shared standards: `docs/STANDARDS_INDEX.md`
- Task skills: `.agents/skills/INDEX.md`

## Durable rule placement

- Shared project rules must be runtime-neutral.
- Put task-specific procedures in `.agents/skills/` and register them in `.agents/skills/INDEX.md`.
- If no current skill owns a durable rule, create a focused skill rather than expanding `AGENTS.md`.
- Agent-specific adapter files, when present, own only themselves. Shared project docs/tests must not enumerate, require, or depend on specific adapter filenames.

Use `.agents/skills/instruction-maintenance/SKILL.md` for instruction structure changes.

## Universal rules

- Never guess or invent; inspect authoritative sources first.
- Challenge assumptions and proposals when evidence, logic, risk, or project constraints warrant it. Do not agree by default or optimise for validating the human; optimise for correctness and better decisions. Do not be contrarian when the evidence supports agreement.
- Keep context and work bounded to the task.
- For work spanning multiple files or likely to run for a while, work in bounded batches: state the current batch, complete and verify it, report progress, then continue.
- Before declaring a required connector/tool/source unavailable, inspect the capabilities exposed by that required connector/tool first.
- Do not add hidden autonomous behaviour, broad discovery loops, or uncontrolled self-improvement.
- Do not add compatibility shims, duplicate implementations, dead code, or outcome-changing fallback/default behaviour unless explicitly approved.
- Do not hardcode hidden choices that belong in config/schema/managed knowledge.
- Heuristics that determine semantic meaning, business outcome, target, permission, or action require explicit human approval; assistive heuristics may only support an authoritative path.
- Runtime safety must be enforced in code/settings/admin, not only instructions.
- Stop/escalate on missing standards, failed validation, unavailable required tools, invalid AI output, or ambiguous high-impact requirements rather than silently choosing another path.
- Do not claim completion without validation evidence.
- Preserve unrelated work from concurrent sessions.
- Before editing, inspect the exact current target file and apply a narrow, context-checked patch.
- If a patch hunk or `old_text` does not match, stop and reread the file before creating a new patch; never retry stale patch text.
- After editing, inspect the diff and run the required validation before reporting completion.

## Finish report

Report what changed, validation performed/result, remaining risk/follow-up, and Git integration state when relevant.
