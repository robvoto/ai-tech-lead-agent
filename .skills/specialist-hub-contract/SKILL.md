---
name: specialist-hub-contract
description: Use when changing run-agent-task, manifest, subprocess statuses, or specialist resume/approval contract behavior.
---

# Skill: Specialist Subprocess Contract

Use when editing the `run-agent-task` boundary or any docs/tests that define how AI Tech Lead talks to a non-interactive caller.

## Rules
- Keep caller-facing orchestration concerns at the subprocess boundary; keep specialist workflow logic inside this repo.
- Treat `run-agent-task` and `manifest` as the only subprocess contract surfaces unless the code proves otherwise.
- Return only supported subprocess statuses from this boundary: `success`, `needs_clarification`, `approval_required`, `failed`.
- If a specialist-internal pause expects human text and the caller should resume later, surface it as `needs_clarification`, not a generic paused or blocked state.
- If a specialist-internal pause expects explicit human approval, surface it as `approval_required` and use the local approval-token flow.
- Keep approval-token issuance and validation local to this repo; the caller should only relay the token back on resume.
- Update the local contract docs and tests in the same change when the status mapping changes.

## Inspect first
- `src/ai_tech_lead/agent_task_runner.py`
- `src/ai_tech_lead/agent_manifest.py`
- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_RUNBOOK.md`

## Validation

```bash
uv run pytest tests/test_agent_manifest.py tests/test_agent_task_runner.py -q
```
