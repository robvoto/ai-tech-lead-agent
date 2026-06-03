# First Run Runbook

## Purpose

Run the local AI Tech Lead project from the correct environment.

There are two separate runs:

1. Normal project run
2. LangGraph Studio visual/debug run

Both must be run from Ubuntu/WSL using the `/mnt/e/...` project path.

## Step 1: Open the project in WSL VS Code

From Ubuntu/WSL:

```bash
cd /mnt/e/Programming/ai-tech-lead
code .
```

Expected VS Code window indicator:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

Expected terminal prompt shape:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

Do not run project runtime commands from:

```text
PS E:\Programming\ai-tech-lead>
```

## Step 2: Confirm the project root

```bash
pwd
```

Expected:

```text
/mnt/e/Programming/ai-tech-lead
```

Use `/mnt/e/...` for runtime commands, not `E:\...`.

## Step 3: Confirm project Python

```bash
uv run python --version
```

Expected:

```text
Python 3.13.x
```

## Step 4: Normal project run

```bash
uv run python -m ai_tech_lead
```

Expected result shape:

```text
AI Technical Lead Assistant started
Project root detected: /mnt/e/Programming/ai-tech-lead
SQLite ready: /mnt/e/Programming/ai-tech-lead/data/ai_tech_lead.sqlite3
```

If a graph sample is wired into the app, additional graph output may appear after the SQLite line.

## Step 5: Validate LangGraph import

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

Expected:

```text
LangGraph OK
```

## Step 6: Understand the package entry point

The project uses a `src` layout:

```text
src/ai_tech_lead/
```

Package name:

```text
ai_tech_lead
```

Correct command:

```bash
uv run python -m ai_tech_lead
```

Incorrect commands:

```bash
uv run python main
uv run python -m main
uv run python -m main.py
```

Those fail because there is no top-level `main` module in the project root.

## Step 7: One-time LangGraph Studio CLI setup

The LangGraph Studio/dev server needs the LangGraph CLI.

Because this project uses uv, do not use raw `pip install` and do not use `uv install`.

Correct one-time project command:

```bash
uv add --dev "langgraph-cli[inmem]"
```

This records the CLI in the project dependency files:

```text
pyproject.toml
uv.lock
```

## Step 8: Confirm Studio environment variables

LangSmith Studio requires a LangSmith API key in the project `.env` file.

The project `.env` file is:

```text
/mnt/e/Programming/ai-tech-lead/.env
```

Required key shape:

```text
LANGSMITH_API_KEY=lsv2...
```

Optional tracing setting:

```text
LANGSMITH_TRACING=true
```

If local testing should avoid sending trace data to LangSmith, use:

```text
LANGSMITH_TRACING=false
```

Do not paste API keys into chat.

## Step 9: Start LangGraph Studio local server

From the WSL project root:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run langgraph dev
```

Expected result shape:

```text
API: http://127.0.0.1:2024
Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
API Docs: http://127.0.0.1:2024/docs
```

Leave this terminal running while using Studio.

## Step 10: Confirm the local server works

Open this locally:

```text
http://127.0.0.1:2024/
```

or:

```text
http://127.0.0.1:2024/docs
```

If this opens, the local LangGraph API server is running.

## Step 11: Connect Studio in APAC LangSmith

Use the APAC Studio connect page for this workspace:

```text
https://apac.smith.langchain.com/o/32ee8d2c-fc6c-4cc0-b5cb-ec93f8c1cb7e/studio/connect?mode=graph
```

Connection settings:

```text
Base URL: http://127.0.0.1:2024
Custom Headers: leave empty
Allowed Domains: localhost, 127.0.0.1
```

Important: delete any blank custom header row. A blank header name/value row can break Studio connection and cause assistant fetch errors.

Do not put the LangSmith API key in Custom Headers. The key belongs in `.env` for the local server.

If Studio asks for graph/interact mode, use graph mode.

If Studio asks to connect to a local server, choose the local server option and provide the base URL above.

## Step 12: Direct Studio URL

If the connect page is already configured, open Studio directly with:

```text
https://apac.smith.langchain.com/studio/thread?baseUrl=http%3A%2F%2F127.0.0.1%3A2024&mode=graph&render=interact
```

The non-APAC equivalent is:

```text
https://smith.langchain.com/studio/thread?baseUrl=http%3A%2F%2F127.0.0.1%3A2024&mode=graph&render=interact
```

Use the APAC URL for this workspace unless there is a specific reason to use the global domain.

## Step 13: Studio graph configuration

Studio graph configuration is in:

```text
langgraph.json
```

Current mapping:

```json
{
  "graphs": {
    "brief_graph": "./src/ai_tech_lead/brief_graph.py:graph"
  }
}
```

This mapping tells Studio which graph to expose.

## Troubleshooting

### `uv: command not found`

You are probably in PowerShell or uv is not available in WSL.

Use a WSL prompt that starts like:

```text
robvoto@LAPOTENTE:
```

### `pip: command not found`

Do not install pip just for this project.

Use uv:

```bash
uv add --dev "langgraph-cli[inmem]"
```

### `uv install` is not recognised

`uv install` is not the right uv command.

Use:

```bash
uv add --dev "langgraph-cli[inmem]"
```

### `No module named main`

Use the package name:

```bash
uv run python -m ai_tech_lead
```

### Studio says `Failed to fetch`

No local API server is reachable at:

```text
http://127.0.0.1:2024
```

Start it:

```bash
uv run langgraph dev
```

Then keep that terminal running.

### Studio says `Failed to fetch assistants` or `Not Found`

First confirm the local server is running:

```text
http://127.0.0.1:2024/
```

If the server is running, check Studio connection settings:

```text
Base URL: http://127.0.0.1:2024
Custom Headers: empty
Allowed Domains: localhost, 127.0.0.1
```

Delete any empty custom header row. This was confirmed to fix the connection.

### LangSmith 403 during local runs

If logs show:

```text
LangSmithError ... 403 Forbidden
```

then LangSmith tracing/upload is being rejected. That is separate from whether the local server is running.

Check the API key and workspace/project access in LangSmith.

Do not paste API keys into chat.
