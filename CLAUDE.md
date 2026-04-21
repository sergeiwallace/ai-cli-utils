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

## ai sync Scope Boundary

`ai sync` handles **only CC session data** — files that are NOT tracked in git:
- `~/.claude/projects/` JSONL conversation files and memory files
- `~/.claude/history.jsonl` (prompt history, path-translated for cross-machine differences)

**Never add config files, scripts, hooks, or handoff queue files to sync.** Those are git-tracked — use `git pull/push` in the owning repo. Syncing git-tracked files via `ai sync` creates dirty working trees and conflicts.

**Statusline script (`~/.claude/statusline-command.sh`)** — lives in `src/ai_cli/data/statusline-command.sh`, deployed by `ai update` as a plain file (any existing symlink is replaced). Do not manage it via `ai sync` and do not keep it in any other project repo.

## Documentation Maintenance

- **Update docs when shipping features** — after any feature lands, update `docs/tools/ai-cli-usage.md` (usage reference), inline code comments in `main.py` for changed commands, and `README.md` if the CLI interface changed. Doc staleness is a bug.
- **Same commit rule** — doc updates ship in the same commit as the feature, not as a follow-up.
- **Plan docs are living docs** — update status, decisions, and approval log as work progresses.

## CLI Conventions

- **All options must have both short and long forms** — e.g. `-f`/`--force`, `-d`/`--dry-run`. No long-only flags (except hidden internal `SUPPRESS` flags passed machine-to-machine).
- When adding a new CLI option, add both forms simultaneously. Do not ship long-only flags.

## Development Workflow

**All feature work follows this pipeline:**

```text
Plan → Implement → /simplify → Checks → UAT → Push to main
```text

### Branch Strategy

- Push directly to `main` — no PRs. Feature branches only when work spans multiple sessions or has a hard human gate mid-flight.
- Atomic commits
- **Commit at working checkpoints** — commit and push at each working checkpoint (feature functional, doc ready, task added, etc.). Don't leave uncommitted changes at end of session.

### Implementation Pipeline

| Phase | Gate |
|-------|------|
| **Plan** | **Human approves plan** before coding starts |
| **Implement** | Tests must pass locally |
| **Simplify** | Run `/simplify` — scope creep, dead code, over-engineering removed |
| **Automated Checks** | **Hard gate** — `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest` must pass. Always run **after** `/simplify` (it modifies code). |
| **UAT** | **Human approves** before pushing |
| **Version bump** | After every task or bug fix, ask: "Minor or patch bump?" Then update CHANGELOG, bump version, tag, publish. |

### Versioning Convention (semver)

- **Minor bump (`0.x.0`)** — significant new features. Not necessarily every task — use judgment. Ask the user after each task completes.
- **Patch bump (`0.x.y`)** — bug fixes only, no new features. Use plain integers: `0.5.1`, `0.5.2`, etc.
- **Never use `.post` suffix** — if a publish fails, bump the patch instead of appending `.postYYYYMMDD...`.
- **CHANGELOG is required** with every bump — one entry per task completed, not summarized. Reference the task ID.
- **After every task or bug fix:** ask "Minor or patch bump?" before closing out. Then bump, tag, publish.
- **Tag format:** `vX.Y.Z` — push tag to trigger the GH Release workflow.

### UAT Presentation Format

```markdown
## UAT Summary

### What was built — [1-3 bullet points]
### Files changed — [list of files created/modified]
### Test results — [test output summary]
### How to verify — [steps the human can take to manually test]
### Acceptance criteria status — [x] Criterion 1 ...
```text

## Document Structure

All markdown docs with 3+ sections must include a `## Table of Contents` as the first section. Use anchor links to all `##` and `###` headings. Plan docs must include an `## Approval Log` section.

## File Naming

- **Standard names** (ALL_CAPS): README, CLAUDE, GEMINI, SKILL, MEMORY, SCRATCH
- **All other markdown docs**: kebab-case (e.g., `api-design.md`, `migration-plan.md`)

## Secrets and Credentials

- **Never run credential-listing commands** — `ntfy token list`, `stripe keys list`, or any CLI subcommand that prints secret values inline. Verify auth by sending a test request and checking the HTTP status code. This extends the global banned-commands policy to service-specific CLIs.

## MCP Servers

Required: `github`, `gemini-cli`.

## Gemini CLI

**Always pass an explicit `model` argument.** Do not rely on defaults. `gemini-3-flash-preview` is the universal safety net.

```text
mcp__gemini-cli__ask-gemini(prompt="...", model="gemini-3-flash-preview")
```text

Models ranked by capability: `gemini-3.1-pro-preview` → `gemini-3-flash-preview` → `gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.5-flash-lite`.
