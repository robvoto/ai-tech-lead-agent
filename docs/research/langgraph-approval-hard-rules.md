---
topic: LangGraph hard rules vs LLM-based approval gates
date: 2026-06-10
sources:
  - https://dev.to/jamesbmour/interrupts-and-commands-in-langgraph-building-human-in-the-loop-workflows-4ngl
  - https://labs.reversec.com/posts/2025/08/design-patterns-to-secure-llm-agents-in-action
  - https://growwstacks.com/blog/human-in-the-loop-ai-agents-langgraph
  - https://towardsdatascience.com/building-human-in-the-loop-agentic-workflows/
  - https://topuzas.medium.com/advanced-langgraph-orchestration-enterprise-ready-ai-workflow-management-54d0e71133c2
---

## Summary
No single standard library or framework pattern exists for "hard rules before LLM" in coding-task approval workflows. The professional community splits into three camps: topology-as-hard-rule (interrupt IS the enforcement), deterministic threshold checks before the LLM (enterprise pattern), and policy in the prompt. Keyword/regex heuristics are explicitly rejected by security researchers as bypassable and brittle.

## Options found

- **Topology as hard rule** — `interrupt_before` in LangGraph IS the non-negotiable gate. The LLM only decides whether to route to it; the interrupt itself cannot be bypassed. LangGraph's own stance.
- **Deterministic threshold + LLM for grey zone** — Hard code a structured threshold (e.g. purchase > $500 always interrupts). The category being checked is structured data, not keyword scanning. Most cited enterprise pattern.
- **Policy in the prompt** — Express hard rules as absolute LLM instructions ("You MUST return needs_approval=true if…"). No code heuristics but trusts the LLM to honour them. Simplest, riskiest.
- **Classify first, then policy table** — A classification node asks the LLM to extract structured action categories. Code then applies a policy table against those categories. No keywords; LLM extracts, code enforces. More nodes, most transparent.

## Recommendation for this project
For the local prototype, **Option A (policy in prompt) is acceptable now** because the existing confidence threshold (< 0.75 → forced approval) and failure fallback already act as safety nets. **Option B (classify first, policy table) is the production-grade path** when hard guarantees matter. Keyword/regex heuristics must not be used.

## Raw notes
- "interrupt on irreversible, high-blast-radius actions only — not on every step" — production rule of thumb
- "defenses that rely on heuristics…can be bypassed in most cases" — security literature
- LangGraph Academy shows the interrupt topology pattern (module 4 research assistant) but does not prescribe the decision logic that triggers it
- Confidence threshold < 0.75 in current risk_reviewer.py already forces approval regardless of LLM decision — this is the closest thing to an existing hard rule
