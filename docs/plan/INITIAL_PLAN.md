# Initial Plan

## Source of truth

This file is the durable planning hook for future chats:

```text
E:\Programming\ai-tech-lead\docs\plan\INITIAL_PLAN.md
```

Future project sessions should read this file before proposing architecture or implementation steps.

Current project root:

```text
E:\Programming\ai-tech-lead
```

WSL path:

```text
/mnt/e/Programming/ai-tech-lead
```

Do not move the repo yet. A possible future native WSL location is:

```text
~/projects/ai-tech-lead
```

## Project identity

Build a local AI Technical Lead Assistant.

The assistant should supervise coding tools rather than replace them.

It should help plan work, prepare execution briefs, review outputs, manage task context, and keep token/cost usage controlled.

A major purpose of this project is to prevent common coding-agent failure modes:

- doing more than requested
- making risky changes without approval
- adding unapproved fallbacks
- preserving useless legacy code
- leaving unused code
- hardcoding small sample heuristics
- skipping help text/docstrings where purpose is not obvious
- claiming completion without evidence

## Confirmed environment

Use the Windows VS Code application opened in WSL mode.

Expected VS Code indicator:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

Runtime commands must run from Ubuntu/WSL using:

```bash
cd /mnt/e/Programming/ai-tech-lead
```

Do not use PowerShell as the project runtime terminal.

Python/runtime:

- Python: 3.13
- Environment/dependency manager: uv
- Dependency files: `pyproject.toml` and `uv.lock`
- No `requirements.txt` unless a specific future deployment/tool requires it

Normal project run:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead
```

LangGraph Studio/dev server run:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run langgraph dev
```

One-time setup for Studio CLI, if missing:

```bash
uv add --dev "langgraph-cli[inmem]"
```

## Current LangGraph Studio status

LangGraph Studio is a separate visual/debug workflow from the normal project run.

Studio connects to a local server at:

```text
http://127.0.0.1:2024
```

Browser URL:

```text
https://smith.langchain.com/studio/thread?baseUrl=http%3A%2F%2F127.0.0.1%3A2024&mode=graph&render=interact
```

Current graph config is in:

```text
langgraph.json
```

Current graph mapping:

```json
{
  "graphs": {
    "brief_graph": "./src/ai_tech_lead/brief_graph.py:graph"
  }
}
```

## Primary goals

- Learn modern AI orchestration
- Build enterprise-relevant AI engineering experience
- Control token and model costs
- Create a demonstrable portfolio project
- Keep the system understandable and locally runnable
- Build a supervising assistant that keeps coding agents scoped, safe, and evidence-based

## Assistant behaviour

The assistant should:

- Read backlog and task context carefully
- Avoid full backlog or full repo reads by default
- Use a compact backlog index/cache where possible
- Fetch only specific rows, tasks, or files when needed
- Create high-level execution briefs for coding agents
- Ask for approval when scope, cost, or risk is unclear
- Monitor token and cost usage
- Update backlog and status notes only with evidence
- Improve skills and Markdown instruction files only after showing proposed changes
- Stop coding agents before broad, unrelated, risky, or unclear work
- Require help text or module/function purpose text where future maintainers need it
- Reject unapproved broad fallbacks, hidden defaults, and sample-only heuristics
- Prefer explicit errors over silent fallback behaviour
- Remove unused code and dead paths instead of preserving legacy code by default

## Preferred stack

- Python 3.13
- WSL2 Ubuntu runtime
- Windows VS Code app with WSL backend
- Deep Agents
- LangGraph ecosystem
- LiteLLM
- uv
- Telegram Bot API
- SQLite

## Initial prototype scope

Start small. The first working version should include:

1. Telegram message input
2. Deep Agent execution
3. One coding model call
4. Basic backlog management
5. One approval workflow
6. Token usage logging
7. Simple persistent memory

## Backlog handling rule

Do not read a whole backlog by default.

Preferred flow:

1. Read compact backlog index/cache
2. Identify likely task IDs or rows
3. Fetch only the specific task detail needed
4. Create a concise execution brief
5. Ask for approval if scope or risk is unclear
6. Update status only after evidence is available

## Coding-agent supervision model

Execution briefs should include:

- Task objective
- Relevant files only
- Constraints
- Acceptance criteria
- Cost/risk notes
- What not to touch
- Expected output format
- Required help text or docstring expectations
- No unapproved fallbacks, heuristics, or legacy compatibility paths

The assistant should review results before accepting them.

The assistant should flag or stop coding-agent work when it sees:

- unrelated file changes
- broad refactors not requested
- fallback logic added without approval
- hardcoded small lists used as general rules
- unused code or dead branches left behind
- missing help text/docstrings where purpose is not obvious
- hidden defaults or swallowed errors
- claims of completion without test or runtime evidence

## Learning workflow

For each baby step:

1. Explain the concept in plain English.
2. Point to the relevant LangChain Academy lesson when possible.
3. Describe the expected input and output in normal language.
4. Let the human code a small version where useful.
5. Review and correct only what is needed.
6. Run the smallest validation.
7. Explain the result.

## Not in initial scope

- RAG
- Complex multi-agent architecture
- Large memory architecture
- Full IDE replacement
- Unbounded retry loops
- Large repository scans without a reason

## Next step

Use LangChain Academy `module-1/simple-graph.ipynb` as the learning guide for the first LangGraph concept: state, node, edge, compile, invoke.

Then build a tiny local graph that turns a user request into a plain execution brief without using an LLM yet.
