# Graph Workflow

This document describes the implemented coding workflow graph in `src/ai_tech_lead/coding_workflow_graph.py`.

## Purpose

The graph turns an explicit human request or backlog item into a bounded coding-agent handoff. It orchestrates the tech lead thinking — clarification, analysis, planning, and execution — while keeping the human in control.

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
-> 1d_collect_research_evidence  (if online research approved)
-> 2_review_risk
-> 3_check_clarification
-> [3b_clarification_interrupt, if task is ambiguous]
-> 3c_tech_lead_analyse          (formulate task + high-level tech direction)
-> [4_approval_interrupt, if approval is needed]
-> 5b_request_plan
-> 5c_review_plan
-> [5d_plan_interrupt, if plan rejected repeatedly or reviewer unavailable]
-> 5_create_agent_instruction
-> 6_run_coding_agent
-> [6b_failure_interrupt, if agent fails twice in a row]
-> 7_end_node
-> END
```

## Tech lead analysis node

`3c_tech_lead_analyse` is the orchestrator's thinking step. It acts as a senior tech lead:

1. Reads the request and any clarification feedback.
2. Considers the project constraints, watched directories, acceptance criteria, risk notes, and research evidence.
3. Produces two outputs stored in graph state:
   - `formulated_task` — 1-2 sentence imperative task statement, passed to the plan request and instruction builder.
   - `brief` — compact high-level technical direction (approach, relevant areas, constraints, what to avoid), passed to the coding-agent instruction.

The orchestrator reasons about the task rather than filling in a template. Low-level implementation details are left to the coding agent.

Prompt: `data/prompts.json` key `tech_lead_analysis`, module `tech_lead_analyst.py`.

## Failure and retry path

After `6_run_coding_agent`:

- **Success** → `7_end_node`.
- **Failure, retry_count < 2** → back to `5_create_agent_instruction` with `coding_agent_correction` included so the agent knows what went wrong.
- **Failure, retry_count ≥ 2** → `6b_failure_interrupt` asks the human for guidance, then back to `5_create_agent_instruction` with human feedback and retry count reset to 0.

## Prompt/module map

| Step | Module | Prompt key |
|---|---|---|
| Clarification check | `clarification_checker.py` | `clarification_check` |
| Tech lead analysis | `tech_lead_analyst.py` | `tech_lead_analysis` |
| Plan request | `coding_workflow_graph.py` | `plan_request_instruction` |
| Plan review | `plan_reviewer.py` | `plan_review` |

## Human interrupt nodes

These nodes call `interrupt(value)` (LangGraph dynamic breakpoint pattern) to pause and wait for human input. The graph resumes via `invoke(Command(resume=value))`.

- `1c_research_interrupt` — emits `{"kind": "research_approval", "question": "..."}`. Resume with `{"approved": True/False}`.
  - `approved: True` continues to `1d_collect_research_evidence`.
  - `approved: False` ends the workflow.
- `3b_clarification_interrupt` — emits `{"kind": "clarification", "question": "..."}`. Resume with a plain text string. Appends to `task_feedback` (Annotated reducer, items accumulate across loops). Loops back to `3_check_clarification`.
- `4_approval_interrupt` — emits `{"kind": "approval", "reason": "...", "formulated_task": "..."}`. Resume with `{"approved": True/False, "approved_by": "..."}`.
- `5d_plan_interrupt` — emits `{"kind": "plan_guidance", "plan_text": "...", "reason": "...", "rejection_count": N}`. Resume with a plain text guidance string. Appends to `task_feedback` and resets plan rejection count.
- `6b_failure_interrupt` — emits `{"kind": "failure_guidance", "coding_agent_result": "...", "retry_count": N}`. Resume with a plain text guidance string. Appends to `task_feedback` and resets retry count to 0.

## Interrupt detection

Read the interrupt value from `state_snapshot.tasks[0].interrupts[0].value`. The `kind` field identifies which interrupt is active.

## Looping behaviour

- Clarification interrupt loops back to `3_check_clarification` after the human replies. The `task_feedback` Annotated reducer accumulates all replies.
- If the research complexity check is disabled, unavailable, or returns invalid output, the workflow now fails closed and routes to the research approval path instead of silently treating the task as simple.
- Research approval rejection ends the workflow before risk review.
- Approved research resumes into bounded evidence collection, then continues to risk review.
- Rejected plans loop back to `5b_request_plan` with LLM correction feedback.
- After two plan rejections, or if the plan reviewer is unavailable, `5d_plan_interrupt` fires instead.
- Failed coding agent runs loop back to `5_create_agent_instruction` with the failure summary included in the instruction.
- After two failures, `6b_failure_interrupt` fires to ask the human for guidance.

## Execution boundary

The graph does not directly edit files. Real file changes can only happen through the configured coding-agent runner after the instruction package is created and execution is enabled by settings.

## Maintenance rule

When graph nodes, routes, interrupts, or state fields change, update this document and `docs/ARCHITECTURE.md` if the system boundary changes.
