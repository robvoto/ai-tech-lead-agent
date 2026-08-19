# Security Policy

## Risk model

AI Tech Lead can inspect repositories, prepare coding instructions, invoke external coding agents, and run validation commands. These capabilities can modify files or expose sensitive information if project, tool, and approval boundaries are not enforced.

## Sensitive material

Do not commit or publish:

- provider API keys, Telegram tokens, cookies, passwords, or webhook secrets;
- local `.env` files or credential stores;
- project-specific private source copied into prompts, fixtures, or logs;
- coding-agent transcripts containing secrets or personal information;
- runtime databases, approval tokens, resume tokens, or operator configuration.

## Project and filesystem scope

- Resolve the project through canonical project metadata.
- Pin the resolved project identity for the lifetime of the task.
- Do not infer or substitute another repository when a path fails.
- Limit reads and writes to files relevant to the approved task.
- Reject attempts to escape the project root or modify unrelated repositories.
- Treat instruction files and skills as bounded project metadata, not unrestricted authority.

## Coding-agent execution

- Keep real execution disabled unless explicitly enabled in local settings.
- Pass a bounded task, expected outcome, relevant constraints, and validation requirements.
- Treat coding-agent output and success claims as untrusted.
- Review the actual changed files before accepting completion.
- Run relevant tests, lint, or focused verification when available.
- Report partial or failed validation accurately.
- Do not permit silent scope expansion, destructive commands, or credential access.

## Inbound subprocess contract

The JSON subprocess contract is the caller-facing boundary into AI Tech Lead. Validate all inbound task data and all structured output. Reject malformed, incomplete, mismatched-project, or unsupported contract versions rather than guessing missing fields.

The configured coding agent is invoked separately by the controlled runner as a CLI subprocess. It does not receive authority merely because the inbound caller supplied a valid JSON task.

## Telegram and interactive interfaces

- Store bot credentials outside source control.
- Configure a non-empty `telegram_allowed_chat_ids` allowlist before enabling Telegram.
- The current implementation treats an empty allowlist as unrestricted access; an empty value is therefore **not** an access control and must not be used in an enabled deployment.
- Treat incoming messages as untrusted task input.
- Apply the same project, approval, and execution controls used by non-interactive requests.
- Do not send private source files, secrets, or full sensitive logs through chat.

## Reporting

Report suspected vulnerabilities privately to the repository owner. Provide a minimal sanitised reproduction and do not attach credentials, private repositories, runtime state, or coding-agent transcripts to an issue.
