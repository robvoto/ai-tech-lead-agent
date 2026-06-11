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
-> 2_review_risk
-> 3_create_brief
-> 3b_check_clarification
-> 3d_formulate_task
-> 4_approval_required, if approval is needed
-> 5b_request_plan
-> 5c_review_plan
-> 5_create_agent_instruction
-> 6_run_coding_agent
-> 7_end_node
-> END
```

## Human gates

The graph can pause before these nodes:

- `1c_research_gate` - asks whether online research is allowed when local cached research is insufficient.
- `3c_clarification_gate` - asks Rob for clarification when the task is ambiguous.
- `4_approval_required` - asks for approval before risky or forced-approval work proceeds.
- `5d_plan_human_gate` - asks Rob for guidance when the coding-agent plan cannot be approved automatically.

## Looping behaviour

- Clarification loops back to `3b_check_clarification` after human feedback.
- Rejected plans can loop back to `5b_request_plan` with correction feedback.
- After repeated plan rejection, the graph asks the human instead of looping forever.

## Execution boundary

The graph does not directly edit files. Real file changes can only happen through the configured coding-agent runner after the instruction package is created and execution is enabled by settings.

## Maintenance rule

When graph nodes, routes, interrupts, or state fields change, update this document and `docs/ARCHITECTURE.md` if the system boundary changes.
