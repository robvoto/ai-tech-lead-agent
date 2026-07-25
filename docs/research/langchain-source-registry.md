---
topic: Approved LangChain / LangGraph source registry
date: 2026-06-16
sources:
  - https://docs.langchain.com/oss/python/langgraph/overview
  - https://docs.langchain.com/oss/python/langgraph/interrupts
  - https://docs.langchain.com/oss/python/langgraph/checkpointers
  - https://docs.langchain.com/oss/python/langgraph/application-structure
---

## Summary

This registry is the bounded default source set for approved LangChain/LangGraph research in this project. Keep the list official, short, and refreshable. If a source falls out of date or stops matching the implemented workflow, replace it intentionally instead of expanding the list.

## Approved sources

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) - Freshness: current overview for graph structure and runtime concepts.
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) - Freshness: current human-in-the-loop and resume pattern reference.
- [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) - Freshness: current persistence and checkpoint reference.
- [LangGraph application structure](https://docs.langchain.com/oss/python/langgraph/application-structure) - Freshness: current guidance for app and graph layout.

## Offline snapshots

Approved online pages may be captured as small offline research notes under `docs/research/` when the project needs a bounded snapshot. Those notes should stay short, source-linked, and easy to refresh through the existing research cache workflow.

## Refresh rules

- Use official docs first.
- Keep the registry bounded; do not add broad web search here.
- Refresh a snapshot only when the source has materially changed or the note is stale.
- Prefer replacing an outdated entry over accumulating duplicates.

## Relationship to runtime source discovery

`research_discovery_enabled` (off by default) lets the graph propose a candidate official source for gaps outside this registry, gated by an explicit per-URL human approval and an outbound-URL safety guard (see `docs/GRAPH_WORKFLOW.md`). It does not add broad web search to this registry — discovered URLs are fetched once, approved individually, and never written back into `research_online_source_urls`/`research_allowed_domains`. Adding an entry to this registry stays a deliberate, manual edit.
