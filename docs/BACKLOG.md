# Backlog

## ATL-001 - Replace stale Telegram placeholder backlog item with current operator baseline

Approval Required: no
Approval Reason: Documentation/backlog cleanup only. No runtime behaviour change.

Goal:
Update the backlog so it reflects the current system: Telegram is now a real operator surface controlled by settings/admin, not a placeholder.

Constraints:
- Do not change runtime code.
- Preserve useful historical context only if it helps current planning.
- Keep the backlog readable and executable by explicit ID.

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
- Ensure Codex receives only relevant identity context through the instruction assembler.
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
- Do not let natural-language routing silently run Codex.
- Keep Codex execution controlled by settings/admin.
- Add tests for plain text routing, `/code`, `/fix` alias behaviour, and unclear intent.

## ATL-004 - Add backlog management capability to the orchestrator

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
