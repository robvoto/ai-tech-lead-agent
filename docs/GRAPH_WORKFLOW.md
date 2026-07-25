# Graph Workflow

This document describes the implemented coding workflow graph in `src/ai_tech_lead/coding_workflow_graph.py`.

## Document split

Use this file for the runtime path of the main coding workflow: node order,
interrupts, resume behavior, and loops.

Use `ARCHITECTURE.md` for stable boundaries such as adapter ownership,
Caller-facing versus AI Tech Lead responsibilities, persistence, and the
subprocess contract.

## Purpose

The graph turns an explicit human request or backlog item into a bounded coding-agent handoff. It orchestrates the tech lead thinking — clarification, analysis, planning, and execution — while keeping the human in control.

Visual preview: [graph_diagram.png](diagrams/graph_diagram.png)



## Studio entrypoint

`langgraph.json` exports:

```text
coding_workflow_graph -> ai_tech_lead.coding_workflow_graph:graph
```

## Main path

```text
START
-> 1_read_request
-> 1a_understand_and_bound_request
-> 1b_resolve_context
-> [7_end_node with clarification, if a reference cannot be resolved safely]
-> 1c_check_research
-> [1c1_discover_research_source, if a knowledge gap needs online sources]
-> 1c_research_interrupt
-> 1d_collect_research_evidence  (if online research approved)
-> 2_review_risk                 (sets risk_level: LOW/MEDIUM/HIGH/UNKNOWN; sleep mode applied here)
-> 3c_tech_lead_analyse          (formulate task + high-level tech direction)
-> [4_approval_interrupt, if approval is needed]
   -> approve: continue below
   -> request changes: back to 3c_tech_lead_analyse (up to 5 revision cycles, then ends)
   -> ask a question: back to 4_approval_interrupt (answer shown, same choices again)
   -> cancel: 7_end_node
-> 5b_request_plan
-> 5c_review_plan
-> [5d_plan_interrupt, if plan rejected repeatedly or reviewer unavailable]
-> 5_create_agent_instruction
-> 6_run_coding_agent
-> [6b_failure_interrupt, if agent fails twice in a row]
-> 6c_verify_completion             (AI Tech Lead verifies the coding agent's success claim)
-> [6d_completion_verification_interrupt, if completion can't be proven automatically]
-> 7_end_node
-> END
```


## Request understanding and context resolution

Before research, the graph now separates the original request from the bounded task context:

- `1a_understand_and_bound_request` classifies the request intent, detects possible external references, and records whether execution was requested. Phrases such as `report only` or `do not code` keep execution intent false.
- `1b_resolve_context` resolves references only from explicit caller-supplied project, resource, or fetched backlog context. It never infers that a prefix such as `AF` means a particular project.
- If a reference cannot be resolved, the workflow asks one precise clarification question and ends before research.
- `bounded_request` contains the original request plus verified project/backlog context. Research, risk review, and tech-lead analysis use this bounded form rather than the raw ambiguous request.

Backlog context is optional. Plain reviews, explanations, bug investigations, and direct coding requests continue without requiring a backlog reference.

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

- **Success** → `6c_verify_completion`. A clean process exit from the coding agent is not the completion rule; verification is.
- **Failure, retry_count < 2** → back to `5_create_agent_instruction` with `coding_agent_correction` included so the agent knows what went wrong.
- **Failure, retry_count ≥ 2** → `6b_failure_interrupt` asks the human for guidance, then back to `5_create_agent_instruction` with human feedback and retry count reset to 0.

## Completion verification

`6c_verify_completion` is the AI Tech Lead's own close-out check, run only after the coding agent has already reported success. It compares the approved task, technical direction (`brief`), plan (`plan_text`), project-level acceptance criteria (`settings.acceptance_criteria`), changed files, and the coding agent's completion report against each other and returns one of three verdicts (`completion_verifier.py`, prompt `completion_verification`):

- `complete` → `7_end_node`. Optional/nice-to-have observations are recorded in `verification_reason` but never block completion.
- `correction_required` → names exactly one unmet requirement in `verification_correction`. If `verification_attempt_count` is below `COMPLETION_VERIFICATION_MAX_CORRECTIONS` (1), it loops back to `5_create_agent_instruction` with that correction folded into the instruction (same mechanism as `coding_agent_correction`) and the attempt count incremented. If the budget is already used, the node instead sets `verification_status` to `failed` and routes to `7_end_node` — no third attempt, the unresolved issue is reported rather than retried again.
- `human_verification_required` → completion can't be proven automatically (for example, it depends on a visual or subjective check). Routes to `6d_completion_verification_interrupt`.

If the AI review is disabled, the LLM call fails, or it returns a malformed response, verification fails closed to `human_verification_required` rather than silently reporting `complete`.

`6d_completion_verification_interrupt` emits `{"kind": "completion_verification", "reason": "...", "coding_agent_result": "...", "changed_files": [...]}`. Resume with `{"decision": "confirm_complete"}` (marks `complete`) or `{"decision": "reject", "text": "..."}` (marks `failed` with the human's reason). Either resume routes straight to `7_end_node` — a human rejection ends the workflow and reports the issue; it does not start another coding-agent attempt.

The subprocess contract (`agent_task_runner.py`) only reports `status: success` when `coding_agent_success` is true **and** `verification_status == "complete"`; a coding agent that exits cleanly but fails verification is reported as `failed` with the verification reason, not as a success. Telegram's backlog auto-close (`_finalize_completed_task`) applies the same rule before marking a backlog item Done.

## Prompt/module map

| Step | Module | Prompt key |
|---|---|---|
| Risk review | `risk_reviewer.py` | `risk_review` |
| Tech lead analysis | `tech_lead_analyst.py` | `tech_lead_analysis` |
| Plan request | `coding_workflow_graph.py` | `plan_request_instruction` |
| Plan review | `plan_reviewer.py` | `plan_review` |
| Completion verification | `completion_verifier.py` | `completion_verification` |
| Operator question answer | `operator_question.py` | `operator_question_answer` |

## Human interrupt nodes

These nodes call `interrupt(value)` (LangGraph dynamic breakpoint pattern) to pause and wait for human input. The graph resumes via `invoke(Command(resume=value))`.

- `1c_research_interrupt` — emits `{"kind": "research_approval", "question": "..."}`. Resume with `{"approved": True/False}`.
  - `approved: True` continues to `1d_collect_research_evidence`.
  - `approved: False` ends the workflow.
- `4_approval_interrupt` — emits `{"kind": "approval", "reason": "...", "formulated_task": "...", "last_question": "...", "last_answer": "..."}` (the last two only present after an `ask_question` round). Resume with `{"action": "approve"|"request_changes"|"ask_question"|"cancel", ...}`:
  - `{"action": "approve", "approved_by": "..."}` — continues to `5b_request_plan`.
  - `{"action": "request_changes", "feedback": "..."}` — appends `feedback` to `task_feedback`, increments `approval_revision_count`, and loops back to `3c_tech_lead_analyse` to regenerate `formulated_task`/`brief`. At `approval_revision_count` = 5 the loop stops and ends the workflow instead of looping again.
  - `{"action": "ask_question", "question": "..."}` — answers the question using the request, resolved project, bounded code context, and research evidence already in state (never crashes the node if the LLM call fails — falls back to a plain "couldn't answer" message), then loops back to `4_approval_interrupt` itself with the Q&A recorded so the same four choices are shown again.
  - `{"action": "cancel"}` (or any unrecognized/malformed resume value — this gate fails closed, never to approve) — ends the workflow.
  - In sleep mode, this node is only reached for HIGH risk or `force_approval=True` tasks.
- `5d_plan_interrupt` — emits `{"kind": "plan_guidance", "plan_text": "...", "reason": "...", "rejection_count": N}`. Resume with a plain text guidance string. Appends to `task_feedback` and resets plan rejection count.
- `6b_failure_interrupt` — emits `{"kind": "failure_guidance", "coding_agent_result": "...", "retry_count": N}`. Resume with a plain text guidance string. Appends to `task_feedback` and resets retry count to 0.
- `6d_completion_verification_interrupt` — emits `{"kind": "completion_verification", "reason": "...", "coding_agent_result": "...", "changed_files": [...]}`. Resume with `{"decision": "confirm_complete"}` or `{"decision": "reject", "text": "..."}`. Either resume ends the workflow at `7_end_node` (never restarts the coding agent).

## Interrupt detection

Read the interrupt value from `state_snapshot.tasks[0].interrupts[0].value`. The `kind` field identifies which interrupt is active.

## Sleep mode (risk-based auto-proceed)

`sleep_mode` is a runtime setting toggled via `/sleep on` or `/sleep off` in Telegram.

When sleep mode is active, the `2_review_risk` node applies these rules instead of always routing to an interrupt:

| risk_level | force_approval | Outcome |
|---|---|---|
| LOW or MEDIUM | false | Proceeds automatically, no interrupt |
| HIGH | any | Routes to `4_approval_interrupt` |
| UNKNOWN | any | Routes to `4_approval_interrupt` (safe default) |
| any | true | Routes to `4_approval_interrupt` |

`risk_level` (LOW/MEDIUM/HIGH/UNKNOWN) is returned by the orchestrator LLM as part of the risk review JSON. UNKNOWN is used when the LLM is disabled, fails, returns low confidence, or returns an unrecognised value.

## Looping behaviour

- `1c_check_research` asks the LLM to name one specific external knowledge gap, not to rate task complexity — a large or architecturally significant task can need no research, and a small task can hinge on one unfamiliar external fact. If no gap is named, research is skipped regardless of task size. The check also sees bounded code context read from `watched_directories` (`research_code_context_enabled`, on by default) so a gap already answered by existing code is not flagged. This code-context scan is scoped to `resolved_project_root` when the task targets a different project than AI Tech Lead itself — it reads the target project's code, not this repo's. The local docs/research cache lookup is unaffected by this and always stays anchored to this repo (`settings.project_root`), since that cache is AI Tech Lead's own shared knowledge base, not project-specific.
- If the knowledge-gap check is disabled, unavailable, or returns invalid output, the workflow fails closed and routes to the research approval path instead of silently treating the task as gap-free.
- `1c1_discover_research_source` only runs when a gap needs online sources. It is off by default (`research_discovery_enabled`). When on, it calls OpenAI's hosted web-search tool to propose one official-documentation candidate for the gap, and every candidate — like every online fetch, static or discovered — must pass the outbound URL safety guard (`research_sources.validate_outbound_research_url`: https-only, no private/loopback/link-local/reserved addresses, redirects re-validated, response size capped) before it is ever shown to a human. If a safe candidate is found, `1c_research_interrupt`'s question names that specific URL; approving it approves fetching only that URL. If discovery is off, errors, or finds nothing safe, the interrupt falls back to the existing bounded-registry question. Discovered URLs are never written back into `research_allowed_domains`/`research_online_source_urls` — growing that permanent list stays a manual config change.
- Research approval rejection ends the workflow before risk review.
- Approved research resumes into bounded evidence collection, then continues to risk review.
- "Request changes" at `4_approval_interrupt` loops back to `3c_tech_lead_analyse` with the feedback appended to `task_feedback`, up to 5 revision cycles (`approval_revision_count`); the 6th "request changes" ends the workflow instead of looping again, so the cycle can't run forever.
- "Ask a question" at `4_approval_interrupt` never leaves that node's loop — it answers, then re-shows the same four choices, and does not touch `formulated_task`, `brief`, `approval_reason`, or research evidence, so no task/project state is lost across the round trip.
- Only "cancel" (or an unrecognised resume value, which fails closed to cancel) ends the workflow from `4_approval_interrupt`.
- Rejected plans loop back to `5b_request_plan` with LLM correction feedback.
- After two plan rejections, or if the plan reviewer is unavailable, `5d_plan_interrupt` fires instead.
- Failed coding agent runs loop back to `5_create_agent_instruction` with the failure summary included in the instruction.
- After two failures, `6b_failure_interrupt` fires to ask the human for guidance.
- A successful coding-agent run is checked once by `6c_verify_completion`; `correction_required` loops back to `5_create_agent_instruction` at most once (`COMPLETION_VERIFICATION_MAX_CORRECTIONS`), after which a repeat failure ends the workflow as `failed` instead of looping again.

## Execution boundary

The graph does not directly edit files. Real file changes can only happen through the configured coding-agent runner after the instruction package is created and execution is enabled by settings.

## Maintenance rule

When graph nodes, routes, interrupts, or state fields change, update this document and `docs/ARCHITECTURE.md` if the system boundary changes.
