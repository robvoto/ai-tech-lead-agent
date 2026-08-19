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
-> 1_read_and_classify_request  (validates the request isn't empty, then checks it's actually
   coding/technical work AI Tech Lead should handle; if not, ends here with a clear reason)
-> 1a_decide_project_scope      (stub for now — always "existing"; new-project handling is a
   separate, not-yet-built capability, see backlog ATL-081)
-> 1b Find Project Context
-> [1b_context_clarification_interrupt, if a reference cannot be resolved safely]
   -> back to 1b Find Project Context with the human's answer folded in (bounded to 1 round;
      still unresolved after that -> 7_end_node, a clear failure, not another interrupt)
-> 1c_check_if_code_look_needed  (cheap check: does this task actually touch existing code?
   scales effort to complexity — most tasks skip the next step entirely)
-> [1c1_codex_reads_code, if needed]  (Codex looks at real code, read-only, reports back in
   plain text; reuses the same run_coding_agent machinery as 5b_request_plan, explicitly
   forced read-only; if Codex is stuck it prefixes its report "NEEDS_HELP:" so that signal
   carries into analysis and, downstream, risk review, instead of being silently lost)
-> 2 Analyse Task              (formulate task + high-level tech direction; runs before the
   research gap check so the gap is judged against the formulated task, not the raw request;
   now also sees Codex's code-look report, when there is one)
-> 2a_check_research            (checks research_checked=False -> runs once per attempt)
-> [2a1_discover_research_source, if a knowledge gap needs online sources]
-> 2a_research_interrupt
-> 2b_collect_research_evidence  (if online research approved)
   -> back to 2 Analyse Task to finalize the task using the collected evidence
      (research_checked is now True, so this second pass goes straight to 3_review_risk
      instead of re-running 2a_check_research)
-> 3_review_risk                 (sets risk_level: LOW/MEDIUM/HIGH/UNKNOWN; sleep mode applied
   here; reviews the formulated task + technical direction, not the raw request, since the
   actual implementation scope is only known once analysis has run)
-> [4_approval_interrupt, if approval is needed]
   -> approve: continue below
   -> request changes: back to 2 Analyse Task (research_checked is already True at this
      point, so this goes straight through to 3_review_risk again rather than re-checking
      research; up to 5 revision cycles, then ends)
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

- `1_read_and_classify_request` validates the request isn't empty, then runs a cheap LLM
  check: is this actually coding/technical work AI Tech Lead should handle at all? Fails open
  (treats the request as relevant) if AI is disabled or the check errors, so infrastructure
  problems never silently block real work.
- `1a_decide_project_scope` is a stub — it always returns "existing" today. Real new-vs-existing
  project detection is a separate, not-yet-built capability (backlog ATL-081).
- `1b Find Project Context` resolves references only from explicit caller-supplied project, resource, or fetched backlog context. It never infers that a prefix such as `AF` means a particular project. The subprocess boundary has already validated and normalized that caller data into AI Tech Lead's immutable internal `TargetProjectContext`, and downstream project-aware steps reuse that same context instead of rebuilding it ad hoc.
- If a reference cannot be resolved, the workflow pauses at `1b_context_clarification_interrupt` and asks one precise clarification question, instead of ending the run. The human's answer resolves only the single reference that was asked about (never parsed or guessed at — recorded verbatim as trusted human context, the same trust level already given to caller-supplied `TargetProjectContext`), and the workflow loops back to `1b Find Project Context` to retry. This is bounded to one clarification round (`CONTEXT_CLARIFICATION_MAX_RETRIES`); if the reference is still unresolved after that, the workflow ends clearly at `7_end_node` instead of asking again — see "Human interrupt nodes" below.
- `bounded_request` contains the original request plus verified project/backlog context (and any clarification answer). Research, risk review, tech-lead analysis, completion verification, and operator Q&A use this bounded form rather than the raw ambiguous request.

Backlog context is optional for the graph itself. Telegram `/code` requests first
produce a structured backlog draft and wait for explicit `/approve`; the item is
saved before the coding workflow starts. Explicit `/run ATL-###` requests continue
to resolve and execute the selected existing backlog item directly.

## Code look node

`1c_check_if_code_look_needed` is a cheap classification: does this task actually change,
fix, or depend on existing code, or is it trivial (docs, config, a brand-new file)? Scales
effort to complexity — most tasks skip the next step. Fails open toward *skipping* if AI is
disabled or the check errors, since this is a cost-saving enhancement, not a safety gate.

`1c1_codex_reads_code` only runs when the check above says yes. It reuses the exact same
`run_coding_agent` call that `5b_request_plan` uses, forced explicitly to `sandbox_override=
"read-only"` — Codex looks, doesn't touch. It reports back in plain text: which files are
relevant, what the existing code does, anything that affects implementation. If Codex is
stuck or uncertain, it's instructed to prefix its report `NEEDS_HELP:` rather than guess —
that signal flows into `2 Analyse Task`'s prompt and, from there, into risk review, rather
than being silently lost. No new human-interrupt gate was added for this in v1; ATL is
expected to fold the concern into its own analysis and let the existing approval/risk-review
path catch anything genuinely risky. Revisit if that proves insufficient in practice.

Design grounding: Anthropic's own multi-agent guidance says to scale effort to task
complexity rather than always or never doing extra work, and specifically warns against
parallel multi-agent fan-out for coding tasks (too interdependent, unlike independent
research threads) — hence one bounded Codex call here, not several.

Note: `5b_request_plan` was also updated to set `sandbox_override="read-only"` explicitly.
It previously relied on Codex's own default and only logged a warning if files changed
unexpectedly instead of preventing it.

## Tech lead analysis node

`2 Analyse Task` is the orchestrator's thinking step. It is re-entered more than once per
task: a preliminary pass runs before `2a_check_research` (to give the research gap check a
formulated task instead of the raw request), a final pass runs after research completes (or
immediately, if no gap was found), and further passes run on each approval "request changes"
revision. It acts as a senior tech lead each time:

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

- `1b_context_clarification_interrupt` — emits `{"kind": "context_clarification", "question": "...", "reason": "...", "retry_count": N}`. Resume with a plain text answer. The answer is stored and the workflow loops back to `1b Find Project Context` to retry resolution with it folded in. Bounded to `CONTEXT_CLARIFICATION_MAX_RETRIES` (1) round — if still unresolved after that, `1b Find Project Context` routes straight to `7_end_node` instead of interrupting again, and the subprocess boundary reports a clear failure (`context_clarification_exhausted`), not another `needs_clarification` that would invite an automated resubmit.
- `2a_research_interrupt` — emits `{"kind": "research_approval", "question": "..."}`. Resume with `{"approved": True/False}`.
  - `approved: True` continues to `2b_collect_research_evidence`.
  - `approved: False` ends the workflow.
- `4_approval_interrupt` — emits `{"kind": "approval", "reason": "...", "formulated_task": "...", "last_question": "...", "last_answer": "..."}` (the last two only present after an `ask_question` round). Resume with `{"action": "approve"|"request_changes"|"ask_question"|"cancel", ...}`:
  - `{"action": "approve", "approved_by": "..."}` — continues to `5b_request_plan`.
  - `{"action": "request_changes", "feedback": "..."}` — appends `feedback` to `task_feedback`, increments `approval_revision_count`, and loops back to `2 Analyse Task` to regenerate `formulated_task`/`brief`. At `approval_revision_count` = 5 the loop stops and ends the workflow instead of looping again.
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

When sleep mode is active, the `3_review_risk` node applies these rules instead of always routing to an interrupt:

| risk_level | force_approval | Outcome |
|---|---|---|
| LOW or MEDIUM | false | Proceeds automatically, no interrupt |
| HIGH | any | Routes to `4_approval_interrupt` |
| UNKNOWN | any | Routes to `4_approval_interrupt` (safe default) |
| any | true | Routes to `4_approval_interrupt` |

`risk_level` (LOW/MEDIUM/HIGH/UNKNOWN) is returned by the orchestrator LLM as part of the risk review JSON. UNKNOWN is used when the LLM is disabled, fails, returns low confidence, or returns an unrecognised value.

## Looping behaviour

- An unresolved reference at `1b Find Project Context` pauses for exactly one human clarification round (`CONTEXT_CLARIFICATION_MAX_RETRIES` = 1). If the answer resolves the reference, the workflow continues to `2 Analyse Task`. If it's still unresolved after that one round, the workflow ends at `7_end_node` and reports a clear failure rather than looping or asking again — this keeps the bound tight even when the caller resuming the task is an automated agent (e.g. Agent Hub) rather than a person.
- `2 Analyse Task` now runs immediately after context resolution, before `2a_check_research`. `route_after_tech_lead_analyse` checks the `research_checked` flag: on the first pass (flag not yet set) it routes to `2a_check_research`; on every later pass through this same node (after research evidence arrives, or after an approval "request changes" revision) the flag is already `True`, so it routes straight to `3_review_risk` instead. This is what lets one node serve as both the preliminary and the final analysis pass without a separate node type.
- `1d_check_project_guidance` runs once, between the code-look step and `2 Analyse Task`, gated by `project_guidance_discovery_enabled` (on by default). It does two things:
  1. Bounded project-guidance discovery (`project_guidance_discovery.py`): a local, non-LLM read of the resolved target root's own `AGENTS.md`, `docs/INDEX.md`, and `.skills/INDEX.md` — the same three files the project-pack bootstrap treats as canonical — plus, when `.skills/INDEX.md` exists, the single best-scoring skill file (scored against its own index entry, not its full content, so nothing beyond the index itself needs to be opened to pick it). Every file is optional; a target with no project pack yet discovers nothing. The skills-index bullet parser accepts two generic line shapes (`` - `path` - summary `` and plain `- path.md: summary`), found necessary when ATL-077's cross-project proof ran this against real repos (Job Hunter, Agent Factory, and a genuinely instruction-light repo) and found Agent Factory's real `.skills/INDEX.md` used the second shape — no project-specific parsing was added, only a second generic bullet shape.
  2. A governed check (`project_guidance_governance.py`) over what was (or wasn't) discovered: an LLM call that decides `sufficient`, `missing`, or `conflicting`, and — for anything other than `sufficient` — whether the gap or conflict actually matters enough to require human review (`requires_review`). A `missing` verdict that doesn't matter for this task does **not** block; `conflicting` always requires review, regardless of what the model itself reports for `requires_review` (conflicting guidance is never silently merged). Like `2a_check_research`'s knowledge-gap check, this fails closed (`conflicting` + `requires_review=True`) if AI is disabled, the call errors, or the response is invalid.
  `route_after_check_project_guidance` sends `requires_review=True` to `1d1_project_guidance_interrupt`, which pauses with a small structured proposal (`kind: "project_guidance_governance"` — status, summary, related existing locations, the smallest proposed change, and why) and resumes with `approved`/`rejected`. Approving folds the proposed change into `project_guidance_notes` for this run only — nothing is ever written back to the target project's own files — and continues to `2 Analyse Task`. Rejecting ends the run at `7_end_node` with a clear failure (mirrors `2a_research_interrupt`'s reject → end behavior) rather than silently proceeding on an admitted gap or unresolved conflict. Everything else (`sufficient`, or `missing` with `requires_review=False`) skips the interrupt entirely and goes straight to `2 Analyse Task`, which reads `project_guidance_notes` from state rather than re-running discovery itself — this whole check runs once per task, not on every later re-entry into `2 Analyse Task` (research loop, approval "request changes" revision).
- `2a_check_research` asks the LLM to name one specific external knowledge gap, not to rate task complexity — a large or architecturally significant task can need no research, and a small task can hinge on one unfamiliar external fact. If no gap is named, research is skipped regardless of task size. The gap check now runs against `formulated_task` (falling back to `bounded_request`/`request` if analysis has not produced one), so the gap reflects the tech lead's own understanding of the task rather than only the raw ask. The check also sees bounded code context read from `watched_directories` (`research_code_context_enabled`, on by default) so a gap already answered by existing code is not flagged. This code-context scan is scoped to `resolved_project_root` when the task targets a different project than AI Tech Lead itself — it reads the target project's code, not this repo's. The local docs/research cache lookup is unaffected by this and always stays anchored to this repo (`settings.project_root`), since that cache is AI Tech Lead's own shared knowledge base, not project-specific. `2a_check_research` always sets `research_checked=True` before routing onward, whether or not a gap was found.
- `3_review_risk` now runs after `2 Analyse Task` rather than before it. It reviews the formulated task plus technical direction (falling back to the raw request only if analysis has not produced a `formulated_task` yet), so risk reflects the actual scope the tech lead decided on rather than the original, sometimes-vaguer request.
- A known trade-off: on the common path (no research gap at all), `2 Analyse Task` still runs twice — once before `2a_check_research`, once immediately after it confirms no gap — since the same node is reused for both the preliminary and final pass. This keeps the node count small and the reasoning consistent, at the cost of one extra `analyse_task` call per attempt versus a design with a dedicated "gap-only" pass. Revisit only if that extra call proves to be a real cost/latency problem in practice.
- If the knowledge-gap check is disabled, unavailable, or returns invalid output, the workflow fails closed and routes to the research approval path instead of silently treating the task as gap-free.
- `2a1_discover_research_source` only runs when a gap needs online sources. It is off by default (`research_discovery_enabled`). When on, it calls OpenAI's hosted web-search tool to propose one official-documentation candidate for the gap, and every candidate — like every online fetch, static or discovered — must pass the outbound URL safety guard (`research_sources.validate_outbound_research_url`: https-only, no private/loopback/link-local/reserved addresses, redirects re-validated, response size capped) before it is ever shown to a human. If a safe candidate is found, `2a_research_interrupt`'s question names that specific URL; approving it approves fetching only that URL. If discovery is off, errors, or finds nothing safe, the interrupt falls back to the existing bounded-registry question. Discovered URLs are never written back into `research_allowed_domains`/`research_online_source_urls` — growing that permanent list stays a manual config change.
- Research approval rejection ends the workflow before risk review.
- Approved research resumes into bounded evidence collection, then continues to risk review.
- "Request changes" at `4_approval_interrupt` loops back to `2 Analyse Task` with the feedback appended to `task_feedback`, up to 5 revision cycles (`approval_revision_count`); the 6th "request changes" ends the workflow instead of looping again, so the cycle can't run forever.
- "Ask a question" at `4_approval_interrupt` never leaves that node's loop — it answers, then re-shows the same four choices, and does not touch `formulated_task`, `brief`, `approval_reason`, or research evidence, so no task/project state is lost across the round trip.
- Only "cancel" (or an unrecognised resume value, which fails closed to cancel) ends the workflow from `4_approval_interrupt`.
- Rejected plans loop back to `5b_request_plan` with LLM correction feedback.
- After two plan rejections, or if the plan reviewer is unavailable, `5d_plan_interrupt` fires instead.
- ATL-078: the guidance `1d_check_project_guidance` selects once is threaded — never re-selected — through every remaining review point, so the plan, the coding agent, and completion review all judge the work against the same thing:
  - `5b_request_plan`'s instruction and `5c_review_plan`'s reviewer both read `project_guidance_notes` from state. The plan-request instruction includes it so the coding agent's own plan can follow it from the start; the reviewer checks the plan against it (implementation ownership, required validation, named patterns) in addition to the core small/specific/testable bar.
  - `5c_review_plan` also reruns `discover_project_guidance` — the same function, not a new one — against the request enriched with the plan's own text (which already names files/areas to touch), bounded to the same three canonical files plus one skill. Anything newly relevant gets merged into `project_guidance_notes`; nothing changes when the plan reveals nothing new.
  - `5_create_agent_instruction` passes the same `project_guidance_notes` into `build_agent_instruction` as its own "Selected project guidance" section — deliberately additional to, not a replacement for, `instruction_assembler.py`'s existing hard-required `AGENTS.md`/`.skills` extraction for the target repo.
  - `6c_verify_completion` also reads `project_guidance_notes`; a real, relevant violation against it is grounds for `correction_required` through the exact same one-bounded-correction path (`COMPLETION_VERIFICATION_MAX_CORRECTIONS`) already used for task/plan/acceptance-criteria violations — not a second correction mechanism.
  - Throughout, guidance may add or narrow what "good" looks like for this project, but every prompt is explicit that it can never excuse skipping required validation, adding an unrequested fallback, or broadening scope — the existing risk-review and approval gates are untouched and remain the actual safety backstop regardless of what any project's guidance says.
  - `1d_check_project_guidance` also records `project_guidance_paths` (which of the canonical files/skill contributed) and `project_guidance_hash` (one content hash over the selected notes) in state — durable via the existing LangGraph checkpoint, no new persistence store. When `4_approval_interrupt` resumes with `approve`, it recomputes the hash and compares; if it changed since selection (the target project's files moved on while the run sat paused), the resume does not silently proceed to `5b_request_plan` on the stale selection. It clears `approved` and routes to `1d_check_project_guidance` instead — the same discovery+governance step, reused, not a new one — which re-selects fresh guidance and flows back through `2 Analyse Task` to `3_review_risk`, requiring a genuine second approval decision before anything proceeds. This data is intentionally ready for ATL-038's compact audit/replay record to pick up once that ticket exists; it does not itself build that record — ATL-038 remains a separate, unbuilt, approval-gated ticket.
- Failed coding agent runs loop back to `5_create_agent_instruction` with the failure summary included in the instruction.
- After two failures, `6b_failure_interrupt` fires to ask the human for guidance.
- A successful coding-agent run is checked once by `6c_verify_completion`; `correction_required` loops back to `5_create_agent_instruction` at most once (`COMPLETION_VERIFICATION_MAX_CORRECTIONS`), after which a repeat failure ends the workflow as `failed` instead of looping again.

## Execution boundary

The graph does not directly edit files. Real file changes can only happen through the configured coding-agent runner after the instruction package is created and execution is enabled by settings.

## Maintenance rule

When graph nodes, routes, interrupts, or state fields change, update this document and `docs/ARCHITECTURE.md` if the system boundary changes.

## Technology-aware research source policy

Online research remains a bounded workflow, not an open-ended agent loop. Before source discovery, AI Tech Lead derives a small research policy from the precise knowledge-gap question and a bounded set of target-repository signal files (`pyproject.toml`, requirements files, `package.json`, and README files). The selected policy is stored in graph state as profile names, trusted domains, and bounded seed URLs so the same decision is reused after approval and resume.

The current built-in profiles cover LangGraph/LangChain, Python, LiteLLM, and Telegram. Request matches take priority over repository signals. Every discovered or configured URL must match a selected trusted official domain, pass the existing SSRF/redirect/response-size guards, and remain within configured source limits. The human approval gate, local-cache-first behaviour, and explicit stop conditions are unchanged. The reusable behaviour rules live in `.skills/technical-research/SKILL.md`; executable limits and enforcement remain in code.
