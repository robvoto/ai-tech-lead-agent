# Environment Decision

## Confirmed current state

The project files live on the Windows drive.

Windows project path:

E:\Programming\ai-tech-lead

WSL view of the same folder:

/mnt/e/Programming/ai-tech-lead

VS Code is currently recognising the folder as a Windows workspace.

There is no confirmed WSL Remote indicator in the VS Code bottom-left status bar.

## Decision for now

Do not assume VS Code is running with a WSL backend.

For now, treat the current working setup as:

- VS Code app: Windows
- VS Code workspace mode: Windows
- Project files: Windows drive
- WSL: available separately through Ubuntu terminal using /mnt/e/Programming/ai-tech-lead

## Practical rule

Run Linux-specific commands from the WSL terminal.

Do not rely on VS Code being in WSL Remote mode unless the UI clearly shows it.

## Future option

Later we may move the repo to native WSL storage:

~/projects/ai-tech-lead

Do not move it yet unless explicitly requested.

## Status

Accepted for now.
