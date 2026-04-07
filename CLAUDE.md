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

## Public Open-Source Package Standards

This is a **public open-source package**. All code, docs, comments, tests, and commit messages must be written for a general audience.

- **No proprietary names** — never reference humanware, aido, or any private platform/tool names in code, docs, comments, or tests
- **No personal identifiers** — no personal names (first or last), usernames, private server IPs/hostnames, or account-specific paths. Use generic placeholders: `user`, `myproject`, `example.com`, `192.0.2.x`
- **No private project names or prefixes** — don't hardcode any project names or session prefixes from personal workflow in source, docs, or tests. Use only fully generic names: `myproject`, `myapp`, `session-1`, `test-session`
- **Generic examples throughout** — all session names, project names, and config values in tests/docs must be obviously placeholder. Nothing that could be mistaken for a real personal workflow artifact.
- **OS portability** — all code must account for Windows, macOS, and Linux differences. No macOS-only assumptions (e.g. `~/Library/`, `pbcopy`, `open`). Use `sys.platform`, `pathlib`, and `os` abstractions. Flag any unavoidably platform-specific code with a comment.
- **Commit messages are public** — same rules apply to git commit messages
- If you catch an existing violation while working, flag it immediately rather than letting it accumulate

## Documentation Maintenance

- **Update docs when shipping features** — after any feature lands, update `docs/tools/ai-cli-usage.md` (usage reference), inline code comments in `main.py` for changed commands, and `README.md` if the CLI interface changed. Doc staleness is a bug.
- **Same commit rule** — doc updates ship in the same commit as the feature, not as a follow-up.
- **Plan docs are living docs** — update status, decisions, and approval log as work progresses.

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
- **Commit at working checkpoints** — commit and push to your feature branch at each working checkpoint (feature functional, doc ready, task added, etc.). Don't leave uncommitted changes at end of session.
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
| **Version bump** | After any P0/P1 feature task reaches `done`: bump minor version, update CHANGELOG, tag, publish to PyPI. After a bug fix: bump patch. Don't batch — ship when ready. |

### Versioning Convention (semver)

- **Minor bump (`0.x.0`)** — any new user-facing feature or command. Ship as soon as the feature's task is `done`.
- **Patch bump (`0.1.x`)** — bug fixes only, no new features.
- **CHANGELOG is required** with every bump — one entry per task completed, not summarized. Reference the task ID.
- **Version bump + CHANGELOG + PyPI publish** is part of the task `done` definition for P0/P1 features, not a deferred ceremony.
- **Tag format:** `vX.Y.Z` — push tag to trigger the GH Release workflow.

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

**Always pass an explicit `model` argument.** Do not rely on defaults. `gemini-3-flash-preview` is the universal safety net.

```
mcp__gemini-cli__ask-gemini(prompt="...", model="gemini-3-flash-preview")
```

Models ranked by capability: `gemini-3.1-pro-preview` → `gemini-3-flash-preview` → `gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.5-flash-lite`.
