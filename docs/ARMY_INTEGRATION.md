# Army Integration — AI Tech Lead as Coding Specialist

AI Tech Lead is a specialist agent in the Agent Army. The army orchestrator routes coding
requests to it via a JSON subprocess contract. Telegram is for humans only.

## How a coding task flows

```mermaid
sequenceDiagram
    actor You
    participant TG as Telegram
    participant Army as Army Orchestrator<br/>(agent-army)
    participant ATL as AI Tech Lead<br/>(ai-tech-lead)
    participant CB as Coding Backend<br/>(Codex / Claude Code)

    You->>TG: "Fix the login bug"
    TG->>Army: message received
    Army->>Army: detect intent → coding task
    Army->>ATL: run-agent-task {execution_mode: instruction_only}

    Note over ATL: Read + research + risk review
    Note over ATL: Check clarification

    alt Needs clarification
        ATL-->>Army: status: needs_clarification<br/>question: "Which login flow?"
        Army-->>TG: relay question
        TG-->>You: "Which login flow?"
        You->>TG: "The OAuth callback"
        Army->>ATL: run-agent-task (with clarification)
    end

    Note over ATL: Tech lead analysis + build instruction
    Note over ATL: Risk reviewer decides — safe or risky?

    alt Risk reviewer says RISKY — needs your approval
        ATL-->>Army: status: approval_required<br/>reason + plan
        Army-->>TG: "This looks risky. Shall I proceed?\n[plan]"
        TG-->>You: approval request
        You->>TG: "yes / proceed"
        Army->>ATL: run-agent-task {human_approved: true, approval_token: ...}
    end

    Note over ATL: Risk reviewer says SAFE — proceed automatically
    ATL->>CB: bounded instruction
    CB-->>ATL: result + changed files

    ATL-->>Army: {status: success, changed_files: [...]}
    Army-->>TG: summary
    TG-->>You: "Done. Changed: src/auth/callback.py"
```

## Army integration contract

### Input (army → ai-tech-lead)

```json
{
  "request_id": "army-generated-uuid",
  "source": "agent-army",
  "project_root": "/mnt/e/programming/job-hunter-agent",
  "task": "Fix the OAuth callback login bug",
  "execution_mode": "instruction_only",
  "requires_human_approval": true
}
```

`execution_mode` is a request, not a grant. Use `execute` only when the army explicitly wants
coding-agent execution and local settings allow it. `human_approved=true` must be paired with the
matching approval token for the same `request_id` and task.

### Output (ai-tech-lead → army)

```json
{
  "request_id": "army-generated-uuid",
  "status": "success | needs_clarification | approval_required | blocked | failed",
  "summary": "Instruction generated. Ready for coding agent execution.",
  "formulated_task": "Bounded task statement",
  "brief": "Technical direction from tech lead analysis",
  "coding_agent_instruction": "Full instruction for the coding backend",
  "backend_used": "none | codex | claude_code | gemini",
  "execution_performed": false,
  "logs": [],
  "evidence": ["LangChain OAuth docs"],
  "next_action": "Submit instruction to coding backend."
}
```

## Standalone mode

AI Tech Lead still works independently via its own Telegram bot. The army integration is
an additional entry point — it does not replace the existing Telegram flow.

```mermaid
flowchart TD
    You([You])
    TG1[Telegram: AI Tech Lead bot]
    TG2[Telegram: Army bot]
    Army[Army Orchestrator]
    ATL[AI Tech Lead]
    CB[Coding Backend]

    You -->|direct coding task| TG1
    TG1 --> ATL

    You -->|any task| TG2
    TG2 --> Army
    Army -->|routing: coding| ATL

    ATL -->|instruction| CB
    CB -->|result| ATL
    ATL -->|response| TG1
    ATL -->|JSON output| Army
    Army -->|response| TG2
```
