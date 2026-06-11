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
- Add an explicit `/code` command, for unequivocal coding-task intent.
- Do not let natural-language routing silently run the configured coding agent.
- Keep coding-agent execution controlled by settings/admin.
- Add tests for plain text routing, `/code`, and unclear intent.

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
Add a final review step after the configured coding agent finishes. The orchestrator should summarize what happened, list changed files, explain whether the coding agent finished successfully in plain English, and then wait for the human to mark the work as accepted or needing more work. When the orchestrator accepts backlog-backed work, update the backlog item status to Done.

Constraints:
- Keep pre-run approval separate from final acceptance.
- Do not auto-mark work as done just because the coding-agent subprocess exited successfully.
- Add explicit Telegram commands or responses such as `/accept` and `/needs-work` only after reviewing the final design.
- Include who approved execution, who performed the work, and who accepted the result.
- Worker coding agents must not mark backlog items as complete unless the task is explicitly about backlog or documentation maintenance.
- Final backlog completion is owned by the orchestrator after human acceptance.
- Backlog-backed tasks should be marked Done only after the orchestrator accepts the result.
- Do not expose raw diffs in Telegram; show a concise file summary and keep detailed review in git diff/logs.
- Add tests for accepted, needs-work, and no-final-review-yet states.

## ATL-014 - Add worker coding-agent instruction pack and skills

Status: Backlog

Approval Guidance:
Human review recommended because this changes what worker coding agents such as Codex, Claude Code, and Gemini receive before they act.

Goal:
Create a clear instruction structure for the worker coding agents themselves. The orchestrator should not rely only on one generated prompt blob; it should have a small, visible, reusable worker-agent instruction pack and skills that define how coding agents plan, ask for clarification, respect backlog ownership, validate work, and report results.

ISsue to solve: I realise that claude (and probably all) seem to use the same The project-level one at /mnt/e/Programming/ai-tech-lead/AGENTS.md, which is pulled in via @AGENTS.md in CLAUDE.md.

So maybe we dont need this or need to be aware of this

Constraints:
- Keep this separate from the orchestrator's own rules and graph logic.
- Do not create a giant uncontrolled AGENTS.md.
- Keep worker-agent instructions small, versioned, and visible in the repo.
- Support multiple worker agents, not only Codex.
- Include rules for planning, uncertainty, validation, changed-file reporting, token/cost discipline, and backlog ownership.
- Worker agents must not mark backlog items complete unless the task is explicitly backlog/documentation maintenance.
- The orchestrator remains responsible for deciding what context is sent to the worker agent.
- Add tests or checks proving the worker-agent instruction pack is included in generated handoffs.

## ATL-015 - Auto-create backlog item before running ad hoc /code tasks

Status: Backlog

Approval Required: yes
Approval Reason: Changes the /code command flow and adds a new human decision point.

Goal:
When the user sends /code <text> <text>, create a backlog draft first (same draft-then-approve flow as /new <idea>), then on approval implement it and mark it as In Progress. This ensures all coding work is tracked in the backlog with no silent ad hoc runs.

Constraints:
- Keep the existing /code entry points; just insert the draft step before execution.
- Reuse the existing backlog draft builder and approval gate.
- Do not break the current /run <backlog-id> flow, for example /run ATL-001.
- The backlog item must be saved before the coding agent starts.
- Low priority — implement after more critical backlog and workflow items are stable.

## ATL-016 - Resolve /new command ambiguity

Status: Backlog

Approval Required: yes
Approval Reason: Changes a user-facing Telegram command and its routing.

Goal:
/new currently does two unrelated things depending on whether an argument is supplied: no argument resets the session, with argument proposes a backlog item. Split into two distinct commands so the intent is always unambiguous.

Constraints:
- Keep /new as the session reset (existing behaviour, no regression).
- Choose a new command name for backlog item proposals (e.g. /item or /propose).
- Update help text, parser, and all call sites.
- Update tests.

## ATL-017 - Install Bubblewrap dependency and resolve Codex CLI path execution errors in WSL

Status: Backlog

Creator: Human
Epic: AI Tech Lead Infrastructure
Type: Chore
Priority: High
Size: S

Approval Required: yes
Approval Reason: This changes the WSL/runtime setup used by controlled coding-agent execution and updates implementation documentation.

Problem:
When running the Codex CLI tool within the WSL development environment at `/mnt/e/Programming/ai-tech-lead`, the process can hang or warn that sandboxing prerequisites are missing.

During the last execution, Codex warned that Bubblewrap was missing from `PATH`, and the process had to be manually interrupted with `Ctrl+C`.

This makes controlled coding-agent execution unreliable.

Outcome:
Codex CLI runs reliably in WSL with the required Bubblewrap sandbox dependency installed.

The AI Tech Lead setup/implementation documentation also explains:
- Bubblewrap is required for Codex sandbox execution in WSL.
- How to verify the dependency is installed.
- What to do if `/mnt/e` Windows-mount latency causes Codex execution delays.
- When to consider running the repo from native Linux home, such as `~/ai-tech-lead`.

## ATL-018 - Add real coding-agent cancellation from Telegram

Status: Backlog

Approval Guidance:
Human review required because this changes process control for the worker coding-agent subprocess.

Problem:
Rob needs a Telegram command, such as `/cancel-code`, that stops the currently running coding-agent subprocess when it is doing the wrong thing or taking too long. `/new` already starts a fresh Telegram session and clears waiting state, but it cannot reliably stop a coding-agent subprocess that is already running if the Telegram polling loop is blocked by that subprocess.

Outcome:
The Telegram operator can request cancellation of the active coding-agent subprocess safely and clearly. The command must mean “stop the coding agent”, not “reject approval”, not “start a new session”, and not “stop the Telegram bot”.

Scope:
- Add an explicit command such as `/cancel-code`.
- Track the active coding-agent subprocess in a safe process-control boundary.
- Allow cancellation while the coding agent is running.
- Report whether cancellation succeeded, failed, or no coding agent was running.
- Keep `/approve` and `/reject` for approval decisions.
- Keep `/new` for fresh-session reset.

Out of Scope:
- Do not reintroduce `/stop` as a user-facing command.
- Do not use `/cancel` ambiguously for both approval rejection and subprocess cancellation.
- Do not kill unrelated processes.

Acceptance Criteria:
- `/approve` approves a waiting decision.
- `/reject` rejects a waiting decision.
- `/new` clears the current Telegram session state.
- `/cancel-code` attempts to stop only the active coding-agent subprocess.
- If no coding agent subprocess is running, `/cancel-code` says so clearly.
- Tests cover successful cancellation, no-active-process behaviour, and no regression to `/approve`, `/reject`, and `/new`.

Acceptance Criteria:
1. Bubblewrap is installed globally in the WSL instance.

   ```bash
   sudo apt update
   sudo apt install bubblewrap -y
   ```

2. The setup documentation explains why Bubblewrap is required for Codex sandbox execution in WSL.

3. The setup documentation includes a verification command, such as:

   ```bash
   which bwrap
   bwrap --version
   ```

4. Documentation includes a troubleshooting note for Codex hangs or slow execution from `/mnt/e`, including Windows-mounted filesystem latency as a possible cause.

5. Documentation states when to consider moving or cloning the repo to native WSL Linux storage, for example `~/ai-tech-lead`, while keeping the current canonical Windows project path clear.

6. A controlled Codex CLI execution is retested after Bubblewrap is installed.

Constraints:
- Do not remove the existing Windows project location from documentation.
- Do not assume `/mnt/e` must be abandoned; document it as a possible latency factor only.
- Do not bypass sandboxing to hide the Bubblewrap warning.
- Keep this as an infrastructure/setup chore, not a feature change.

