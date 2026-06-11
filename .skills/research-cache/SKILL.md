---
name: research-cache
description: Use when doing web research or designing heuristics/rules/patterns. Check the cache first, save findings after.
---

# Skill: Research Cache

## Purpose
Avoid redundant web fetches and reinventing solutions that professionals have already established.

## Before doing any web research
1. Read `docs/research/INDEX.md`.
2. If a matching entry exists, read that file — do not re-fetch the web.
3. If no match, proceed with web research, then save findings (see below).

## After completing web research
1. Create `docs/research/<slug>.md` using the template below.
2. Add one line to `docs/research/INDEX.md` under the correct category.

## File template

```markdown
---
topic: <short topic name>
date: <YYYY-MM-DD>
sources:
  - <url 1>
  - <url 2>
---

## Summary
<2-4 sentence plain-language summary of the professional consensus>

## Options found
<bullet list of distinct approaches with tradeoffs>

## Recommendation for this project
<what we decided and why>

## Raw notes
<key quotes or details worth preserving>
```

## Rules
- One file per research topic. Update existing files rather than creating duplicates.
- Keep summaries short — this is a cache, not a report.
- Always record sources so findings can be reverified later.
- If a cached finding is more than 6 months old and the topic evolves fast (LLM tooling, APIs), note it may be stale before relying on it.
