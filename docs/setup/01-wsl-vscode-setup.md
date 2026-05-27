# WSL, VS Code, and Git Setup

## Final working model

Use the Windows VS Code application, but open this project in WSL mode.

The correct VS Code window title/explorer should show:

```text
AI-TECH-LEAD [WSL: UBUNTU]
```

The correct terminal prompt should look like:

```text
robvoto@LAPOTENTE:/mnt/e/Programming/ai-tech-lead$
```

If the terminal prompt starts with `PS E:\Programming\ai-tech-lead>`, that is PowerShell and is not the project runtime terminal.

## Current folder location

Windows path:

```text
E:\Programming\ai-tech-lead
```

WSL path to the same folder:

```text
/mnt/e/Programming/ai-tech-lead
```

## How to open correctly

From a WSL Ubuntu terminal:

```bash
cd /mnt/e/Programming/ai-tech-lead
code .
```

This should open VS Code with the WSL backend.

## Python extension

Install the Microsoft Python extension in the WSL VS Code window.

The extension does not install Python for the project.

It gives VS Code Python editing support, import resolution, interpreter selection, debugging, testing support, and Pylance/code intelligence inside the WSL environment.

## Interpreter rule

The project interpreter is managed by uv and lives here:

```text
/mnt/e/Programming/ai-tech-lead/.venv/bin/python
```

VS Code can use this interpreter only when the workspace is opened in WSL mode.

If VS Code shows Windows interpreters such as `AppData\Local\Python`, the window is not using the desired project backend.

## Runtime rule

Do not run project commands from PowerShell.

Run from the WSL terminal:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python -m ai_tech_lead
```

## Python command distinction

In WSL:

```bash
python3 --version
```

shows Ubuntu system Python. It may show Python 3.12.3.

That is not the project runtime.

The project runtime is checked with:

```bash
uv run python --version
```

Expected result:

```text
Python 3.13.x
```

## Do not manually activate the environment

Do not use this as the normal workflow:

```bash
source .venv/bin/activate
```

Use uv instead:

```bash
uv run python -m ai_tech_lead
```

## Git over SSH from WSL

Use Git over SSH from WSL for this repo.

SSH keys live in the WSL user home directory, not in the project folder:

```text
/home/robvoto/.ssh
```

Key files:

```text
/home/robvoto/.ssh/id_ed25519
/home/robvoto/.ssh/id_ed25519.pub
```

Meaning:

- `id_ed25519` is the private key. Never share it.
- `id_ed25519.pub` is the public key. This is the one added to GitHub.
- `known_hosts` stores trusted SSH server fingerprints such as GitHub.

Useful permission commands:

```bash
chmod 750 ~
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
```

SSH authentication test:

```bash
ssh -T git@github.com
```

Successful result shape:

```text
Hi robvoto! You've successfully authenticated, but GitHub does not provide shell access.
```

The `does not provide shell access` part is normal.

## Git remote rule

Check the remote with:

```bash
git remote -v
```

The remote must contain the real GitHub owner and repository name, not a placeholder like `USERNAME`.

Correct shape:

```text
origin  git@github.com:robvoto/codingAssistant.git (fetch)
origin  git@github.com:robvoto/codingAssistant.git (push)
```

If the remote contains `USERNAME`, fix it with:

```bash
git remote set-url origin git@github.com:robvoto/codingAssistant.git
```

Important distinction:

- `ssh -T git@github.com` working means the SSH key is accepted.
- `ERROR: Repository not found` means the repo URL or repo access is wrong.

## Validation commands

From the WSL VS Code terminal:

```bash
cd /mnt/e/Programming/ai-tech-lead
uv run python --version
uv run python -c "from langgraph.graph import StateGraph, START, END; print('LangGraph OK')"
uv run python -m ai_tech_lead
git remote -v
ssh -T git@github.com
```

## Future option

Later we may move the repo to native WSL storage:

```text
~/projects/ai-tech-lead
```

Do not move it yet unless explicitly requested.
