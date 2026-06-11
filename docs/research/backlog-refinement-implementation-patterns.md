---
topic: Backlog refinement with cache-first implementation-pattern research
date: 2026-06-10
sources:
  - https://docs.langchain.com/oss/python/langchain/structured-output
---

## Summary
Backlog refinement should use a cache-first flow: read `docs/research/INDEX.md` before any online lookup, reuse cached notes where they apply, and only go online when the cache is missing, stale, or insufficient. The refined backlog item should be emitted as structured output so the system can validate fields instead of parsing freeform prose.

## Options found
- Structured output schema from the model, validated in code.
- Freeform prose drafts with post-processing.
- Search-first research with no local cache. This was rejected because it repeats work and makes the refinement flow harder to audit.

## Recommendation for this project
Use schema-validated JSON or dataclass-style structured output for backlog refinement, keep the research cache small and bounded, and make duplicate, stale, and already-done checks explicit in the item itself. The cache should be the first lookup step, and the refinement flow should mark external research as needed when cached notes do not cover the implementation pattern.

## Raw notes
- LangChain structured output returns predictable JSON, Pydantic models, or dataclasses and validates them before returning.
- Provider-native structured output is the most reliable option when available; tool-calling structured output is the fallback for other models.
- Keep the cache lightweight and file-backed. Do not turn it into a full RAG system.
- Reject hidden heuristics for duplicate, stale, and already-done checks.
- Re-check note freshness when the cache ages past roughly 90 days or the model/provider behavior changes.
