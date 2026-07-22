---
name: result-reporting
description: Reusable runtime rule for reviewable completion and structured result summaries across projects.
---

# Skill: Result Reporting

Use for every coding-agent completion path regardless of target repo.

## Rules
- Summaries must be reviewable by a human and reusable by automation.
- Include changed files, validation result, and remaining risk or follow-up.
- Do not trust free-form success prose alone when structured evidence is available.
- If validation did not run, say that explicitly and why.
- Keep the final result concise, factual, and easy to compare across different coding-agent backends.
