# ATL vs Codex — Who Sees What, Who Does What

## Why this file exists

Rob is the "lead" directing Claude the way ATL should direct Codex:
Rob sets direction, checks work, catches mistakes — but doesn't need to
read every line of code Claude touches. **ATL should work the same way
with Codex.** ATL is the manager. Codex is the coding expert with real
code access. This file tracks, one workflow step at a time, whether the
real code actually works that way — and lists every gap we find.

## Source we're using as the standard

LangChain's official guide, "Context Engineering":
https://www.langchain.com/blog/context-engineering-for-agents

It names 4 ways to manage what an AI sees:
- **Write** — save info outside the AI's view, for later.
- **Select** — only pull in what's actually needed right now.
- **Compress** — shrink big information down to the essential parts.
- **Isolate** — keep different AIs' information separate from each other.

ATL should mostly be doing **Select** (small, narrow peeks) and
**Isolate** (Codex gets the real code access, ATL doesn't).

## Other places to check as we go
- `docs/ARCHITECTURE.md` — the project's own stated boundaries
- `docs/GRAPH_WORKFLOW.md` — the step-by-step workflow, already updated
- `src/ai_tech_lead/instruction_assembler.py` — what Codex actually receives
- Project backlog (ATL-037, ATL-068) — prior decisions about how much
  context ATL should carry

---

## Who does each step — simple table

| Step | Who does it | Notes |
|---|---|---|
| `1_read_and_classify_request` | Deterministic validation + ATL (LLM) | Rebuilt this session: merged old `1_read_request` + `1a Understand Request` into one node. Validates non-empty, then a cheap LLM call checks "is this actually ATL's job (coding/technical)?" Fails open if AI is off. |
| `1a_decide_project_scope` | Stub (no AI yet) | New this session. Always returns "existing" for now. Real new-vs-existing detection is ATL-081, not built. |
| `1b Find Project Context` | Deterministic code (no AI) | Misleading name — it doesn't search anything. Only uses data the caller already supplied. Never touches the codebase itself. Confirmed: the project list it checks against is a simple flat JSON file already owned by ATL, but it never writes new entries back. |
| `1c_check_if_code_look_needed` | ATL (LLM), cheap | New. Decides if Codex needs to actually look at code before analysis. Fails open toward skipping. |
| `1c1_codex_reads_code` | **Codex**, read-only | New. Only runs if needed. Real code access, but explicitly read-only — reuses Request Plan's own machinery. Reports back in plain text. |
| `2 Analyse Task` | ATL (LLM) | Writes the task brief. Now sees Codex's code-look report when there is one. |
| `2a_check_research` | ATL (LLM) | Peeks at 5 filenames, 800 chars each. |
| Approval Q&A | ATL (LLM) | Same peek, but picks files using the wrong text (gap). |
| Plan Review | ATL (LLM) | No code access at all — judges the plan on logic only. |
| Handoff package build (`instruction_assembler.py`) | Deterministic code (no AI) | Just assembles text sections — no LLM call inside it. |
| Actual coding (`6_run_coding_agent`) | **Codex** | Real code access, real edits. Receives zero code content from ATL, works it out itself. |

This table is the answer to "who does it" — worth carrying into the
diagram or docs later so it's visible at a glance, not just written here.

## Findings so far

### Step: `1_read_and_classify_request` (was two nodes: `1_read_request` + `1a Understand Request`)
- **Status: Rebuilt this session.**
- **Dead-code finding:** the old `1a Understand Request` node computed
  intent, execution-requested, and detected references — but nothing
  downstream ever read them. `1b Find Project Context` silently
  recomputes its own version from the raw request internally. That
  output was pure waste. Removed those fields entirely rather than carry
  dead state forward.
- **Why merged:** the three original nodes (`1_read_request`, `1a`, `1b`)
  ran in a fixed straight line with no branch between them and no LLM in
  two of the three — a textbook case for merging, per LangGraph node-design
  guidance (split only when something could branch, fail independently, or
  needs different info).
- **New real capability added:** a cheap LLM call now checks "is this
  actually AI Tech Lead's job?" (coding/technical work vs. something
  unrelated). Fails open (assumes relevant) if AI is off or the call
  errors, so this can never silently block real work due to an
  infrastructure hiccup.
- **Real bug found and fixed during this work:** this new LLM call, if
  left unpatched in tests, made a genuine live network call to OpenAI on
  every single graph test — this sandbox has a real API key and real
  network access. Added a default test fixture (`conftest.py`,
  `_disable_relevance_check_ai_by_default`) so all tests fail open by
  default; only tests that specifically want to test the AI path override
  it. Without this, test runs would have made unpredictable, unbudgeted
  live API calls.

### Step: `1a_decide_project_scope` (new stub)
- **Status: Built as a stub, not real logic.**
- Always returns `"existing"`. Exists so future work (ATL-081, new/
  unregistered project handling) has a clear place to plug in without
  re-wiring the graph again.

### Step: `1b Find Project Context` — additional finding this session
- Confirmed the project list it checks (`project_registry`) is a real,
  simple, flat JSON list already owned by ATL's own settings — not
  something Agent Hub sends. Confirmed **Agent Hub itself does zero
  pre-processing** — it just routes raw text to ATL, so ATL must own the
  new-vs-existing and context-finding decisions entirely by itself.
- Confirmed the registry **never writes back** new entries — read-only,
  static. If discovery ever gets built (ATL-081), the found project
  should be written back so it's known next time. Not built.
- If a project ever needs real discovery (scanning disk/GitHub) rather
  than a registry lookup, that should be delegated to **Codex**, not
  given to ATL directly — same boundary as everywhere else in this file.

### Step: `2a_check_research` (the "does this need research" check)
- **Status: Checked**
- What ATL sees: up to 5 file names, picked by keyword match, 800
  characters each (about a paragraph).
- Purpose: just to avoid asking "should I research X" when the code
  already answers X. Not meant for real understanding.
- Verdict: matches the "Select" pattern correctly — a small, narrow peek,
  not real code access.
- **Gap found:** LangChain's own guide warns that for code specifically,
  filename-only keyword matching gets unreliable as a codebase grows —
  they recommend also using grep/content search and a "which result is
  actually most relevant" re-ranking step. ATL currently has neither.
  Low priority for this one specific check, but worth remembering if this
  same method gets reused anywhere more important.

---

### Step: Approval Q&A (when Rob asks ATL a question mid-approval)
- **Status: Checked**
- What ATL sees: same trick as above — up to 5 files, 800 chars each.
- **Gap found:** the files are picked by matching the *original task
  request*, not the actual question Rob just typed. So if Rob asks a
  follow-up question about something specific, ATL's file-picker doesn't
  adjust to that question — it's still matching the old task wording.
  This could mean ATL answers a specific question using the wrong files.

### Step: Plan Review (ATL checks Codex's plan before coding starts)
- **Status: Checked**
- What ATL sees: only the task it formulated + the plan text Codex wrote.
  **No file reading at all.**
- Verdict: this is the pattern done right. ATL judges the plan on its own
  logic (does it match the task?) without needing real code access —
  same way Rob reviews Claude's plan without reading every line first.
- No gap here.
- **Bug found and fixed here too (this session, unrelated to the initial
  Plan Review check):** `request_plan_node` called Codex without ever
  setting `sandbox_override` — it never explicitly told Codex "read-only."
  The code already had a warning for exactly this ("Plan request
  unexpectedly changed files") instead of preventing it. Fixed alongside
  the code-look build below, since both call the exact same underlying
  function.

### Step: The handoff to Codex (`instruction_assembler.py`)
- **Status: Checked — this is the most important finding so far.**
- What Codex receives: the task in plain English, ATL's technical
  direction in plain English, project "house rules" as text, which
  folders it's allowed to touch, stop conditions, a time limit.
- **Codex receives ZERO actual code content from ATL.** No file text, no
  snippets. Codex goes and reads/edits the real code itself, using its
  own tools, completely independently.
- Verdict: this confirms the model Rob wants — ATL directs in English,
  Codex does the real code work on its own. No gap here. This is the
  cleanest example in the whole workflow.

### Step 5/6: Codex reads code before Analyse Task — BUILT this session
- **Status: Built and tested.** (Previously logged as "designed, not
  implemented" — now done.)
- New nodes: `1c_check_if_code_look_needed` (cheap classifier, fails open
  toward *skipping*) → `1c1_codex_reads_code` (only if needed; read-only,
  reuses `request_plan_node`'s exact `run_coding_agent` machinery).
- Codex's report now feeds into `2 Analyse Task` via a new `code_recon`
  section in its prompt — `analyse_task()` was updated to accept it.
- If Codex is stuck, it prefixes its report `NEEDS_HELP:` rather than
  guess. No new human-interrupt gate was built for this in v1 — that
  signal flows into ATL's analysis and, from there, into risk review,
  rather than pausing the graph directly. Revisit if that proves
  insufficient in practice.
- **Also fixed while here:** `request_plan_node` now explicitly sets
  `sandbox_override="read-only"` (previously logged as open gap #3 below
  — now closed, since this node's read-only call reuses the exact same
  code path).
- **Real test-hygiene bug found and fixed, same shape as before:** the
  new `check_code_look_need_node`, left unpatched, would have made a real
  live OpenAI call in every graph test in this environment. Extended the
  same `conftest.py` autouse fixture (already built for the relevance
  check) to also cover `code_look_checker.load_settings`.

### Prompt caching — checked, mostly already fine, one open item
- ATL's LLM calls (`orchestrator_llm.py`) are raw HTTP calls to OpenAI's
  Responses API — **not** using LangChain's model classes, even though
  LangGraph is used for the workflow graph itself. Worth knowing since it
  means LangChain's caching middleware doesn't apply here at all.
- OpenAI's prompt caching is automatic (longest-prefix match, no code
  needed) as long as static instructions stay at the start of the prompt
  string and the variable content (the request) comes last.
- **Checked: existing prompts already follow this correctly** (confirmed
  in `risk_review`'s template). The new `atl_relevance_check` prompt
  written this session follows the same convention. No fix needed here.
- **Provider-portability flag (per Rob's instruction — always check this
  before assuming a fix is done):** this "automatic" caching is
  OpenAI-specific behavior. If ATL ever switches to Anthropic, caching
  needs *explicit* `cache_control` breakpoints added at the same
  static/variable boundary — the current prompt-ordering discipline sets
  that transition up well, but it's not automatic on Anthropic. DeepSeek's
  caching behavior is **unverified** — needs research before assuming
  anything, not guessed here.

## Still to check (node by node)

- [ ] (Smaller, optional) Skill selection during handoff reads a skills
      index file — worth a quick look later, lower priority than the above.

## Open gaps log (things we can't/haven't fixed yet — not a to-do list for right now)

1. Code-context selection uses filename keywords only, no content search,
   no re-ranking. (Found at: research check step. Severity: low so far.)
2. When Rob asks ATL a follow-up question, ATL still picks files based on
   the original task wording, not the question itself. (Found at:
   approval Q&A step. Severity: medium — could give wrong-file answers.)
3. DeepSeek's prompt-caching behavior is unverified — needs research
   before ATL could safely switch providers and assume caching still
   works the same way. (Severity: low, future-facing only.)
4. Codex code-look reports don't currently escalate to a human interrupt
   if Codex flags "NEEDS_HELP:" — the signal only flows into ATL's own
   analysis/risk review. Intentionally kept simple for v1; revisit if
   that proves insufficient. (Severity: low, by design for now.)
