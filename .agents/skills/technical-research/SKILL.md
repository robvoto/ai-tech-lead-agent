---
name: technical-research
description: Use when a plan depends on external technical facts, current framework behaviour, API contracts, or official implementation guidance.
---

# Skill: Technical Research

## Purpose
Resolve only the technical uncertainties that materially affect the plan or implementation. Keep research bounded, evidence-linked, and subordinate to the target repository.

## Required sequence
1. Inspect the target repository, tests, documentation, backlog item, and relevant recent changes first.
2. State one precise knowledge-gap question. Do not research broad topics without a decision-relevant gap.
3. Check local project documentation and `docs/research/INDEX.md` before any online lookup.
4. Select official-source profiles from the task and bounded repository signals.
5. Prefer the technology owner's official documentation, official API reference, changelog, release notes, or original repository documentation.
6. Ask for approval before online retrieval and name the exact candidate URL when discovery finds one.
7. Save concise findings with source, date, question, conclusion, assumptions, and impact on the plan.
8. Stop when the question is answered with sufficient evidence or when configured source/query limits are reached.

## Hard rules
- Code enforces source count, timeouts, response-size limits, SSRF protection, trusted domains, approval, caching, and stop conditions.
- Do not browse indefinitely, crawl sites, or expand into adjacent questions.
- Do not use blogs, forums, aggregators, or generated summaries when an official/primary source is available.
- Do not treat more context as better context. Keep only findings that change a decision, constraint, risk, or acceptance criterion.
- If trusted sources conflict or remain insufficient, stop and report the uncertainty instead of guessing.
- Update an existing research-cache note rather than creating a duplicate.
