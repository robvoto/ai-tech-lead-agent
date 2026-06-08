---
name: instruction-maintenance
description: Use when editing AGENTS.md, skills, handoff docs, or other project instruction files.
---

# Skill: Instruction Maintenance

Use when editing project instructions, agent rules, skills, or handoff documentation.

## Purpose
Keep instructions useful, small, current, and non-contradictory.

## Rules
- Put universal operational rules in `AGENTS.md`.
- Put area-specific operational rules in `.skills/<area>/SKILL.md`.
- Put human/developer explanations, rationale, examples, and learning notes in `docs/*`.
- Do not duplicate the same rule across many files.
- Do not copy domain-specific rules from another project unless they apply here.
- Keep agent-facing instruction files concise. If examples or explanations become long, move them to a separate reference document.
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
- Why the rule belongs there
- Evidence source if the change came from official/current guidance
- Any follow-up review needed
