# WSL and VS Code Setup

## Confirmed state

The VS Code app is Windows.

The workspace is currently recognised by VS Code as a Windows workspace.

There is no confirmed WSL Remote indicator in the VS Code bottom-left status bar.

## Current folder location

Windows path:

E:\Programming\ai-tech-lead

WSL path to the same Windows folder:

/mnt/e/Programming/ai-tech-lead

## Practical working model

Use Windows VS Code for editing.

Use the WSL Ubuntu terminal for Linux runtime commands when needed.

From WSL:

cd /mnt/e/Programming/ai-tech-lead

## Do not assume

Do not assume VS Code is using a WSL backend unless the UI clearly shows a WSL Remote indicator.

Do not repeat Remote WSL validation as a next step unless specifically troubleshooting VS Code integration.

## Future option

Later we may move the repo to native WSL storage:

~/projects/ai-tech-lead

Do not move it yet unless explicitly requested.
