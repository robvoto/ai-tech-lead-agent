# Coding-Agent Handoff Rules

These rules are sent to worker coding agents such as Codex, Claude Code, or Gemini.

- Keep the handoff compact and explicit: task, brief, project identity, selected rules, selected skills, allowed directories, stop conditions, and validation expectations.
- Complete only the assigned task. Keep the change small.
- Report changed files, why the design is appropriate, and the exact validation command and result.
- Stop if files outside the allowed directories are needed.
- Stop if the task conflicts with project rules.
- Stop if validation cannot be run or cannot be explained.
- Safety gates such as execution disablement, hidden Telegram work, secret exposure, and approval enforcement stay in Python code, not in prompt text.
- Backlog ownership is defined in the separate backlog ownership rules.
