# Backlog

## ATL-001 - Document current Telegram operator baseline

Status: Done
Validation: Documentation-only backlog update. Wording was aligned with the current Telegram operator model described in `docs/ARCHITECTURE.md`.

Approval Required: no
Approval Reason: Documentation/backlog cleanup only. No runtime behaviour change.

Goal:
Capture the current Telegram operator baseline in the backlog: Telegram is a real operator surface, controlled by settings/admin, with explicit approval gating and phone-first operator messages. Keep this entry as the canonical replacement for the old placeholder framing so future planning stays aligned with the live system.

Constraints:
- Do not change runtime code.
- Preserve useful historical context only if it helps current planning.
- Keep the backlog readable and executable by explicit ID.
- Do not reintroduce placeholder-only Telegram wording.

## ATL-002 - Add LLM risk-review node

Status: Done
Validation: `uv run pytest tests/test_app_settings.py tests/test_risk_reviewer.py -q` passed in WSL.

Approval Required: yes
Approval Reason: This changes the approval model and introduces an LLM decision point.

Goal:
Replace the temporary explicit backlog approval flag with a proper LLM risk-review node that returns a structured approval recommendation.

Constraints:
- Do not use keyword heuristics.
- Return structured fields such as needs_approval, reason, confidence, and recommended human action.
- Keep human approval mandatory before any real coding-agent execution.

## ATL-003 - Add orchestrator identity and mission profile

Status: Done
Validation: `py_compile` passed for touched Python files. Identity inclusion was added to the instruction assembler test.

Approval Required: yes
Approval Reason: This affects the core behaviour and framing of every future agent handoff.

Goal:
Create a small first-class orchestrator identity that states what the system is, why it exists, what it must optimise for, and what it must never do. The identity should be used by the orchestrator and instruction assembler without becoming a giant personality prompt.

Constraints:
- Keep identity concise and operational.
- Do not add vague personality text.
- Include purpose, operating boundaries, cost discipline, human approval, and learning/portfolio goals.
- Store identity in a maintainable place such as a dedicated Markdown profile or validated settings-backed profile.
- Ensure the configured coding agent receives only relevant identity context through the instruction assembler.
- Add tests proving the identity is loaded and included in handoff output.

## ATL-009 - Add natural-language Telegram intent router with explicit code shortcut

Status: Done
Validation: `uv run pytest tests/test_telegram_operator.py tests/test_telegram_intent_router.py -q` passed in WSL.

Approval Required: yes
Approval Reason: This changes the Telegram command model and introduces LLM-based intent routing.

Goal:
Allow normal Telegram text to be handled by the orchestrator AI first, while keeping a small set of explicit commands for safety and clarity.

Constraints:
- Plain text should go through an intent-router step before any action.
- The router may classify messages as ask/question, backlog proposal, code task, status/help, or unclear.
- Risky or code-changing work must still ask for approval.
- Keep explicit `/status`, `/help`, `/approve`, and `/reject` commands.
- Add an explicit `/code` command, or keep `/fix` only as an alias, for unequivocal coding-task intent.
- Do not let natural-language routing silently run the configured coding agent.
- Keep coding-agent execution controlled by settings/admin.
- Add tests for plain text routing, `/code`, `/fix` alias behaviour, and unclear intent.

## ATL-004 - Add backlog management capability to the orchestrator

Status: Done
Validation: `uv run pytest tests/test_telegram_operator.py tests/test_backlog_repository.py tests/test_backlog_draft_builder.py -q` passed in WSL.

Approval Required: yes
Approval Reason: This adds a new management capability and changes how work is created and maintained.

Goal:
Allow the orchestrator to help create, validate, and append backlog items using the current Markdown backlog as the source of truth, while preparing for a future Google Sheets backlog source.

Constraints:
- Do not silently choose work.
- Require explicit human confirmation before adding or changing backlog items.
- Validate required fields: ID, title, approval required, approval reason, goal, and constraints.
- Keep Markdown as the current implementation.
- Design the backlog access behind an interface so Google Sheets can replace Markdown later.
- Add tests for adding a valid item and rejecting an incomplete item.

## ATL-005 - Prepare backlog storage abstraction for future Google Sheets source

Status: Done
Validation: `uv run pytest tests/test_telegram_operator.py tests/test_backlog_repository.py tests/test_backlog_draft_builder.py -q` passed in WSL.

Approval Required: yes
Approval Reason: This introduces a boundary that will affect future backlog reads/writes.

Goal:
Introduce a small backlog repository abstraction so the current Markdown backlog can later be swapped for a Google Sheets-backed backlog without rewriting graph or Telegram command logic.

Constraints:
- Do not integrate Google Sheets yet.
- Do not add OAuth, connector code, or external API calls yet.
- Keep the current Markdown repository working.
- Make the interface explicit: load by ID, list items, add item, and validate item.
- Add tests around the interface using the Markdown implementation.

## ATL-008 - Improve admin save controls and graph runtime logs

Status: Done
Validation: Python graph compile passed. Admin UI basic check passed. WSL pytest wrapper timed out through connector; rerun directly in WSL if needed.

Approval Required: no
Approval Reason: UI/logging observability change only. No graph route or execution behaviour change.

Goal:
Make the admin page easier to use and make graph logs clearer for learning and runtime diagnosis.

Constraints:
- Save button must be visible without scrolling.
- List row delete buttons must use trash-can text/icon.
- Graph logs must visually separate steps.
- Logs must say whether a step is a LangGraph node or a routing decision.
- Logs must show active AI channel/model, brief purpose, instruction purpose, instruction preview, and token/cost availability.

## ATL-007 - Clean up Telegram approval/status messages

Status: Done
Validation: Targeted Telegram operator test added for compact approval text.

Approval Required: no
Approval Reason: Local message formatting only. No graph or execution behaviour change.

Goal:
Make Telegram operator messages short, human-readable, and suitable for phone use.

Constraints:
- Do not dump raw graph state, raw request text, or internal risk-review prompt text into Telegram.
- Keep approval messages to a small number of lines.
- Preserve detailed reasons in graph state/logs.
- Add tests for compact Telegram approval messages.

## ATL-006 - Improve admin UI layout and usability

Status: Done
Validation: Admin JavaScript basic file check passed: no BOM and remove button text present. Visual browser review still required.

Approval Required: no
Approval Reason: Local UI improvement only. It should not change graph, Telegram, or coding-agent execution behaviour.

Goal:
Make the admin screen decent and easier to use, with clearer grouping, spacing, status messages, and safer controls for Telegram and coding-agent execution.

Constraints:
- Keep HTML, CSS, and JavaScript separated.
- Do not add frontend framework complexity.
- Do not hardcode hidden settings.
- Keep settings driven by validated JSON/API data.
- Make dangerous toggles visually clear, especially Execute coding agent.
- Keep the page usable locally on desktop.
- Add or update tests only if existing code supports practical UI/API validation.

## ATL-010 - Review LangGraph persistence and approval resume behaviour

Approval Required: yes
Approval Reason: This affects human approval, execution recovery, and graph state durability.

Goal:
Review whether the current graph uses the right checkpointer/persistence model for Telegram approvals, rejected tasks, resumed approvals, and failed coding-agent handoffs.

Constraints:
- Do not weaken human approval.
- Do not expose raw state in Telegram.
- Keep state raw and bounded.
- Add tests for approve, reject, resume, and stale approval cases.

## ATL-012 - Add per-session LLM cost accumulator to Telegram agent

Approval Required: no
Approval Reason: Observability-only change. No execution or routing behaviour change.

Goal:
Track a running USD cost total across all LLM calls in a Telegram session and include it in the terminal log line, completing the format: `[LLM] purpose  $cost  (x in + y out tokens)  session: $total`. Reset the accumulator when the operator sends /new.

Constraints:
- Accumulate session cost in TelegramOperator and reset it on /new and session reset.
- Pass the running total into _log_usage so the log line includes `session: $total`.
- Do not persist cost across process restarts.
- Do not expose cost figures in Telegram operator messages.
- Add a test proving the session total accumulates across multiple calls and resets on /new.

## ATL-011 - Add canonical AGENTS.md and Claude import compatibility

Status: Done
Validation: Documentation-only change. Verified by file creation/update through local project connector.

Approval Required: no
Approval Reason: Documentation/instruction hygiene only. No runtime behaviour change.

Goal:
Create a concise root AGENTS.md and a CLAUDE.md that imports it, so Codex, Claude Code, Gemini, and other coding agents receive the same project rules.

Constraints:
- Do not duplicate long instructions.
- Keep AGENTS.md concise and operational.
- Move repeatable workflows into `.skills`.
- Keep runtime settings and hard safety gates in code, not only in instruction files.

## ATL-013 - Add final human acceptance after coding-agent run

Approval Required: yes
Approval Reason: This changes the coding workflow lifecycle and adds a second human decision point after the coding agent runs.

Goal:
Add a final review step after the configured coding agent finishes. The orchestrator should summarize what happened, list changed files, explain whether the coding agent finished successfully in plain English, and then wait for the human to mark the work as accepted or needing more work.

Constraints:
- Keep pre-run approval separate from final acceptance.
- Do not auto-mark work as done just because the coding-agent subprocess exited successfully.
- Add explicit Telegram commands or responses such as `/accept` and `/needs-work` only after reviewing the final design.
- Include who approved execution, who performed the work, and who accepted the result.
- Worker coding agents must not mark backlog items as complete unless the task is explicitly about backlog or documentation maintenance.
- Final backlog completion is owned by the orchestrator after human acceptance.
- Do not expose raw diffs in Telegram; show a concise file summary and keep detailed review in git diff/logs.
- Add tests for accepted, needs-work, and no-final-review-yet states.
