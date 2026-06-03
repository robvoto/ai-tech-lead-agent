---
name: instruction-maintenance
description: Use when editing AGENTS.md, skills, handoff docs, or other project instruction files.
---

# Skill: Instruction Maintenance

Use when editing project instructions, agent rules, skills, or handoff documentation.

## Purpose
Keep instructions useful, small, current, and non-contradictory.

## Rules
- Put universal rules in `AGENTS.md`.
- Put area-specific rules in `.skills/<area>/SKILL.md`.
- Do not duplicate the same rule across many files.
- Do not copy domain-specific rules from another project unless they apply here.
- Keep skill files concise. If examples become long, move them to a separate reference document.
- Remove stale claims when proven wrong.
- If unsure whether a rule is still true, mark it for review instead of rewriting it as fact.
- Do not turn backlog items into operating rules.

## Safe edit pattern
1. Inspect the current instruction file first.
2. Make a small targeted edit.
3. Preserve useful intent while removing noise.
4. Report exactly what changed and what was left alone.

## Finish format
Report:
- Instruction files changed
- Rule added/removed/clarified
- Any follow-up review needed
