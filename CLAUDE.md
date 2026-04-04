# Instructions

<!--
  Lean config — for humanware platform users where ~/projects/CLAUDE.md provides shared rules.
  If you are NOT on the humanware platform, use CLAUDE-full.md instead:
    1. Delete or rename this file
    2. Rename CLAUDE-full.md to CLAUDE.md
-->

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Core | Python |
| AI orchestration | Claude Code CLI + Gemini CLI |

## Memory Management

**When saving memories, ALWAYS do both steps — never skip the index update:**

1. Write the memory file to `memory/` with frontmatter (name, description, type)
2. **Immediately** add a pointer to `MEMORY.md` — if it's not indexed, it's invisible in future sessions

## Compaction

**PreCompact hook** fires automatically before compaction — run `/save-state` immediately when you see the `SAVE_STATE_REQUIRED` message. This saves session state to memory files and docs before context is lost.

## CI / Badge Health

After every push to `main`, verify both badges are healthy before presenting a summary to the user:

1. **CI badge** — run `gh run list --repo sergeiwallace/ai-cli-utils --limit 1` and confirm the latest run is `success`. If still `in_progress`, wait and re-check.
2. **Codecov badge** — run `gh run view <run-id> --log | grep "queued for processing"` to confirm upload succeeded. Codecov typically takes 1-3 minutes to reflect; wait if needed, then check `https://codecov.io/gh/sergeiwallace/ai-cli-utils` via `gh api` or curl.

If CI is failing or Codecov is not 100%, fix before closing out the session. If there is a legitimate reason coverage is below 100% (e.g. an optional dependency path that can't be tested), flag it explicitly in the summary message for discussion — do not silently accept degraded coverage.

## Test Requirements

### Python

- Test names: `test_{given}_{when}_{then}`
- Use pytest fixtures for shared setup
- `reviewer` audits test quality on every review (see `.claude/agents/reviewer.md`)
- Run: `pytest`
- **`# pragma: no cover` is a hard human gate** — never add it autonomously. If a line cannot be covered, document it in the UAT report with the specific line, why it can't be mocked, and options. Wait for explicit user approval before adding any pragma.

## CLI Conventions

- **All options must have both short and long forms** — e.g. `-f`/`--force`, `-d`/`--dry-run`. No long-only flags (except hidden internal `SUPPRESS` flags passed machine-to-machine).
- When adding a new CLI option, add both forms simultaneously. Do not ship long-only flags.

## Development Workflow

**All feature work follows this pipeline:**

```
Feature Branch → Plan → Implement → /simplify → Checks → UAT → PR → Merge
```

### Branch Strategy

- All dev work branches from `main`: `feature/short-description`
- Never push feature branches directly to `main` — always PR
- Atomic commits
- Non-dev changes (docs, tooling, markdown) commit directly to `main`

### Implementation Pipeline

| Phase | Gate |
|-------|------|
| **Plan** | **Human approves plan** before coding starts |
| **Implement** | Tests must pass locally |
| **Simplify** | Run `/simplify` — scope creep, dead code, over-engineering removed |
| **Automated Checks** | **Hard gate** — `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest` must pass. Always run **after** `/simplify` (it modifies code). |
| **UAT** | **Human approves** before PR |
| **PR** | Open PR to `main` |

### UAT Presentation Format

```
## UAT Summary

### What was built — [1-3 bullet points]
### Files changed — [list of files created/modified]
### Test results — [test output summary]
### How to verify — [steps the human can take to manually test]
### Acceptance criteria status — [x] Criterion 1 ...
```

## Document Structure

All markdown docs with 3+ sections must include a `## Table of Contents` as the first section. Use anchor links to all `##` and `###` headings. Plan docs must include an `## Approval Log` section.

## File Naming

- **Standard names** (ALL_CAPS): README, CLAUDE, GEMINI, SKILL, MEMORY, SCRATCH
- **All other markdown docs**: kebab-case (e.g., `api-design.md`, `migration-plan.md`)

## MCP Servers

Required: `github`, `gemini-cli`.

## Gemini CLI

**Always pass an explicit `model` argument.** Do not rely on defaults. `gemini-3.0-flash` is the universal safety net.

```
mcp__gemini-cli__ask-gemini(prompt="...", model="gemini-3.0-flash")
```

Models ranked by capability: `gemini-3.1-pro-preview` → `gemini-3.0-pro` → `gemini-3.0-flash` → `gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.5-flash-lite`.
