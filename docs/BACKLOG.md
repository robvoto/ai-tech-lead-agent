# Backlog

## JH-001 - Add Telegram input placeholder

Approval Required: no
Approval Reason: Safe placeholder task. It does not connect the real Telegram API or use secrets.

Goal:
Create the first placeholder for receiving a Telegram message.

Constraints:
- Do not connect Telegram API yet.
- Do not add external secrets.
- Keep it local and testable.

## ATL-002 - Add LLM risk-review node

Approval Required: yes
Approval Reason: This changes the approval model and introduces an LLM decision point.

Goal:
Replace the temporary explicit backlog approval flag with a proper LLM risk-review node that returns a structured approval recommendation.

Constraints:
- Do not use keyword heuristics.
- Return structured fields such as needs_approval, reason, confidence, and recommended human action.
- Keep human approval mandatory before any real coding-agent execution.
