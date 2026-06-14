# Graph Workflow

This document describes the implemented coding workflow graph in `src/ai_tech_lead/coding_workflow_graph.py`.

## Purpose

The graph turns an explicit human request or backlog item into a bounded coding-agent handoff. It must keep the human in control and avoid hidden autonomous execution.

## Studio entrypoint

`langgraph.json` exports:

```text
coding_workflow_graph -> ai_tech_lead.coding_workflow_graph:graph
```

## Main path

```text
START
-> 1_read_request
-> 1b_check_research
-> [1c_research_interrupt, if complex task needs online sources]
-> 2_review_risk
-> 3_create_brief
-> 3b_check_clarification
-> [3c_clarification_interrupt, if task is ambiguous]
-> 3d_formulate_task
-> [4_approval_interrupt, if approval is needed]
-> 5b_request_plan
-> 5c_review_plan
-> [5d_plan_interrupt, if plan rejected repeatedly or reviewer unavailable]
-> 5_create_agent_instruction
-> 6_run_coding_agent
-> 7_end_node
-> END
```

## Plan Sources

The plan handoff is intentionally compact, and the code/prompt pairing is:

- Task formulation: `src/ai_tech_lead/task_formulator.py` uses `data/prompts.json` key `task_formulation`.
- Plan request: `src/ai_tech_lead/coding_workflow_graph.py` uses `data/prompts.json` key `plan_request_instruction`.
- Plan review: `src/ai_tech_lead/plan_reviewer.py` uses `data/prompts.json` key `plan_review`.

## Human interrupt nodes

These nodes call `interrupt(value)` (LangGraph dynamic breakpoint pattern) to pause and wait for human input. The graph resumes via `invoke(Command(resume=value))`.

- `1c_research_interrupt` — emits `{"kind": "research_approval", "question": "..."}`. Resume with `{"approved": True/False}`.
  - `approved: True` continues to `2_review_risk`.
  - `approved: False` ends the workflow without continuing to the risk review.
- `3c_clarification_interrupt` — emits `{"kind": "clarification", "question": "..."}`. Resume with a plain text string. Appends to `task_feedback` (Annotated reducer, items accumulate across loops).
- `4_approval_interrupt` — emits `{"kind": "approval", "reason": "...", "formulated_task": "..."}`. Resume with `{"approved": True/False, "approved_by": "..."}`.
- `5d_plan_interrupt` — emits `{"kind": "plan_guidance", "plan_text": "...", "reason": "...", "rejection_count": N}`. Resume with a plain text guidance string. Appends to `task_feedback` and resets plan rejection count.

## Interrupt detection

Read the interrupt value from `state_snapshot.tasks[0].interrupts[0].value`. The `kind` field identifies which interrupt is active.

## Looping behaviour

- Clarification interrupt loops back to `3b_check_clarification` after the human replies. The `task_feedback` Annotated reducer accumulates all replies.
- Research approval rejection ends the workflow before risk review.
- Rejected plans loop back to `5b_request_plan` with LLM correction feedback.
- After two rejections, or if the plan reviewer is unavailable, `5d_plan_interrupt` fires instead.

## Execution boundary

The graph does not directly edit files. Real file changes can only happen through the configured coding-agent runner after the instruction package is created and execution is enabled by settings.

## Maintenance rule

When graph nodes, routes, interrupts, or state fields change, update this document and `docs/ARCHITECTURE.md` if the system boundary changes.
