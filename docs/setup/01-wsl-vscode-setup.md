# WSL, VS Code, and Git Setup Runbook

## Purpose

This runbook explains how to open and run the project correctly using:

- Windows VS Code application
- WSL Ubuntu backend
- uv-managed Python environment
- Git over SSH from WSL

The goal is to avoid mixing Windows PowerShell, Windows Python, WSL Python, and project Python.

## Step 1: Confirm the project paths

Windows path:

```text
E:\Programming\ai-tech-lead
```

WSL path to the same folder:

```text
/mnt/e/Programming/ai-tech-lead
```

Use the WSL path for terminal/runtime commands.

## Step 2: Install the VS Code WSL extension

In the Windows VS Code app, install this extension:

```text
WSL
```

Publisher:

```text
Microsoft
```

This extension is required so VS Code can open a project with a WSL backend.

Without this extension, `code .` from Ubuntu may not open the project correctly in WSL mode.

## Step 3: Open the project from WSL

Open an Ubuntu/WSL terminal and run:

```bash
cd /mnt/e/Programming/ai-tech-lead
code .
```

Expected result:

VS Code opens a window showing:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

If VS Code opens as a normal Windows workspace instead, close it and reopen from the WSL terminal using `code .`.

## Step 4: Confirm the correct terminal

In the VS Code terminal, the prompt should look like:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

That means the terminal is WSL Ubuntu.

Wrong terminal example:

```text
PS E:\Programming\ai-tech-lead>
```

That is Windows PowerShell. Do not use it for project runtime commands.

## Step 5: Install the Python extension in the WSL window

When VS Code asks:

```text
Do you want to install the recommended Python extension from Microsoft?
```

Choose:

```text
Install
```

The Python extension does not install Python for the project.

It gives VS Code Python editing support inside WSL:

- import resolution
- interpreter selection
- Pylance/code intelligence
- debugging
- test discovery

## Step 6: Select the project interpreter

In the WSL VS Code window:

1. Press `Ctrl+Shift+P`
2. Run `Python: Select Interpreter`
3. Choose the project interpreter under `.venv`

Expected interpreter path:

```text
/mnt/e/Programming/ai-tech-lead/.venv/bin/python
```

If VS Code only shows Windows paths like `AppData\Local\Python`, the window is not in WSL mode.

## Step 7: Understand the Python commands

This command shows Ubuntu system Python:

```bash
python3 --version
```

It may show:

```text
Python 3.12.3
```

That is not the project runtime.

This command shows the project Python managed by uv:

```bash
uv run python --version
```

Expected result:

```text
Python 3.13.x
```

Use `uv run python`, not plain `python3`, for project commands.

## Step 8: Run the project

From the WSL VS Code terminal:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead
```

Expected result shape:

```text
AI Technical Lead Assistant started
Project root detected: /mnt/e/Programming/ai-tech-lead
SQLite ready: /mnt/e/Programming/ai-tech-lead/data/ai_tech_lead.sqlite3
```

## Step 9: Validate LangGraph import

Run:

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

Expected result:

```text
LangGraph OK
```

If this works but VS Code still shows a red underline on `langgraph`, the issue is editor/interpreter selection, not the runtime.

## Step 10: Do not manually activate the environment

Do not use this as the normal workflow:

```bash
source .venv/bin/activate
```

Use uv instead:

```bash
uv run python -m ai_tech_lead
```

The environment folder is still here:

```text
/mnt/e/Programming/ai-tech-lead/.venv
```

But uv manages it.

## Step 11: Use Git over SSH from WSL

Use Git from the WSL terminal.

SSH files live in the WSL user home directory:

```text
/home/robvoto/.ssh
```

Important files:

```text
/home/robvoto/.ssh/id_ed25519
/home/robvoto/.ssh/id_ed25519.pub
/home/robvoto/.ssh/known_hosts
```

Meaning:

- `id_ed25519` is the private key. Never share it.
- `id_ed25519.pub` is the public key. Add this to GitHub.
- `known_hosts` stores trusted server fingerprints such as GitHub.

## Step 12: Validate GitHub SSH

Run:

```bash
ssh -T git@github.com
```

Successful result shape:

```text
Hi robvoto! You've successfully authenticated, but GitHub does not provide shell access.
```

The `does not provide shell access` part is normal.

## Step 13: Check the Git remote

Run:

```bash
git remote -v
```

Correct shape:

```text
origin  git@github.com:robvoto/codingAssistant.git (fetch)
origin  git@github.com:robvoto/codingAssistant.git (push)
```

Wrong shape:

```text
origin  git@github.com:USERNAME/codingAssistant.git (fetch)
origin  git@github.com:USERNAME/codingAssistant.git (push)
```

Fix a placeholder remote with:

```bash
git remote set-url origin git@github.com:robvoto/codingAssistant.git
```

## Step 14: Push changes

Check status:

```bash
git status
```

Stage only intended files:

```bash
git add path/to/file
```

Commit:

```bash
git commit -m "Describe the change"
```

Push:

```bash
git push -u origin main
```

## Troubleshooting

### `uv: command not found`

You are probably in PowerShell or uv is not installed in WSL.

Check the prompt. Correct prompt starts with:

```text
robvoto@LAPOTENTE:
```

Wrong prompt starts with:

```text
PS E:\
```

### `Permission denied (publickey)`

GitHub does not know the SSH key being used.

Check:

```bash
cat ~/.ssh/id_ed25519.pub
```

Add that public key to GitHub SSH keys.

### `ERROR: Repository not found`

SSH authentication worked, but the repository URL is wrong or inaccessible.

Check:

```bash
git remote -v
```

Look for placeholder values like `USERNAME`.

### Red underline on `from langgraph.graph import ...`

First prove runtime works:

```bash
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
```

If runtime works, fix VS Code interpreter selection in the WSL window.
