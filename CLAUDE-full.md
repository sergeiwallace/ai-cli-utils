# Instructions

<!--
  CLAUDE-full.md — standalone Claude Code session config for external contributors.

  If you are NOT using the humanware platform (~/projects/CLAUDE.md), use this file:
    1. Delete or rename CLAUDE.md
    2. Rename this file to CLAUDE.md

  If you ARE on the humanware platform, use CLAUDE.md (the lean version) instead —
  ~/projects/CLAUDE.md provides all the shared rules below.
-->

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Core | Python |
| AI orchestration | Claude Code CLI + Gemini CLI |

## Terminology

- **Session config** — files that shape every Claude Code session: `CLAUDE.md`, `MEMORY.md`, `.claude/settings.json`, `.claude/hooks/`
- **Orchestration config** — files that define multi-agent team setup: `.claude/agents/*.md`, `docs/designs/orchestration.md`, `.githooks/`

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

## Common Patterns

- **Scope creep**: Implement only what the spec requires. Do not add features, defensive abstractions, or "nice to have" improvements not in the spec.
- **Config over code**: Prefer configuration files over hardcoded values.
- **Test behavior, not implementation**: Tests assert outcomes through the public API.
- **Persist research**: When doing research (API comparisons, architecture spikes, technology evaluations), save findings to `docs/research/{topic-slug}.md`. Update existing docs rather than creating duplicates.

## Public Open-Source Package Standards

This is a **public open-source package**. All code, docs, comments, tests, and commit messages must be written for a general audience.

- **No proprietary names** — do not reference private platform or tool names in code, docs, comments, or tests
- **No personal identifiers** — no personal names, usernames, private server IPs/hostnames, or account-specific paths. Use generic placeholders: `user`, `myproject`, `example.com`, `192.0.2.x`
- **Generic examples throughout** — all session names, project names, and config values in tests/docs must be obviously placeholder
- **OS portability** — all code must account for Windows, macOS, and Linux differences. Use `sys.platform`, `pathlib`, and `os` abstractions. Flag any unavoidably platform-specific code with a comment.
- **Commit messages are public** — same rules apply to git commit messages
- If you catch an existing violation while working, flag it immediately

## Documentation Maintenance

- **Update docs when shipping features** — after any feature lands, update `docs/tools/ai-cli-usage.md` (usage reference), inline code comments in `main.py` for changed commands, and `README.md` if the CLI interface changed. Doc staleness is a bug.
- **Same commit rule** — doc updates ship in the same commit as the feature, not as a follow-up.
- **Plan docs are living docs** — update status, decisions, and approval log as work progresses.

## CLI Conventions

- **All options must have both short and long forms** — e.g. `-f`/`--force`, `-d`/`--dry-run`. No long-only flags (except hidden internal `SUPPRESS` flags passed machine-to-machine).
- When adding a new CLI option, add both forms simultaneously. Do not ship long-only flags.

## Test Requirements

### Python

- Test names: `test_{given}_{when}_{then}`
- Use pytest fixtures for shared setup
- `reviewer` audits test quality on every review (see `.claude/agents/reviewer.md`)
- Run: `pytest`
- **`# pragma: no cover` is a hard human gate** — never add it autonomously. If a line cannot be covered, document it with the specific line, why it can't be mocked, and options. Wait for explicit user approval before adding any pragma.

## Development Workflow

**All feature work follows this pipeline:**

```text
Feature Branch → Plan → Implement → /simplify → Checks → UAT → PR → Merge
```text

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

### AI Slop Checklist (enforced by /simplify)

Before presenting for UAT, verify none of these exist:

- Unnecessary wrapper types or abstractions not in the spec
- Builder patterns where a simple constructor suffices
- Error types that will only ever have one variant
- Defensive code for scenarios the spec explicitly excludes
- Verbose comments restating what the code already says
- Feature flags or configuration for things that don't vary
- TODO/FIXME comments used as placeholders

### UAT Presentation Format

When presenting for UAT, always use this template:

```markdown
## UAT Summary

### What was built — [1-3 bullet points]
### Files changed — [list of files created/modified]
### Test results — [test output summary]
### How to verify — [steps the human can take to manually test]
### Acceptance criteria status — [x] Criterion 1 ...
```text

## Document Structure

All markdown docs with 3+ sections must include a `## Table of Contents` as the first section — immediately after the title/header block, before any other content. Use anchor links to all `##` and `###` headings. Plan docs must include an `## Approval Log` section tracking decisions. Update plan docs as work progresses — they are living documents.

## File Naming

- **Standard names** (ALL_CAPS): README, CLAUDE, GEMINI, SKILL, MEMORY, SCRATCH
- **All other markdown docs**: kebab-case (e.g., `api-design.md`, `migration-plan.md`)

## MCP Servers

Required: `github`, `gemini-cli`.

## AI Orchestration

Claude is the lead — it owns the conversation, writes code, uses tools, and makes final decisions. Gemini is a specialist invoked via `mcp__gemini-cli__ask-gemini` for research, review, and analysis. Agent teams coordinate multiple Claude agents for parallel work.

### Collaboration Principles

- **Think independently** — Don't default to agreement. Push back on risks, complexity traps, or poor architecture.
- **Radical candor** — Correct factual errors and challenge flawed assumptions directly.
- **Be an advisor, not an executor** — Engage as a thinking partner. Say what you'd actually recommend and why.
- **Calibrate pushback to stakes** — Cosmetic choices: just do it. Architectural decisions: push back hard if you disagree.
- **Frame effort in Claude Code time** — Always give execution time (e.g., "~5 minutes of Claude Code time"), not human developer time. Call out human action steps separately.

### Operating Principles

1. **Single responsibility.** Each agent does one thing.
2. **Read-write separation.** Only `engineer` can modify code. All others are read-only.
3. **Research before implementation.** Two-phase workflows beat jumping straight to code.
4. **Cross-validation for high-stakes decisions.** Get independent perspectives and synthesize.
5. **External context once at the start.** Read docs or API specs once. Don't re-fetch per agent.
6. **Human gates at decision points.** Pause for approval before committing, PRs, or architecture changes.
7. **Tasks as the coordination primitive.** Agents coordinate through a shared task list.
8. **Right-size your team.** Start with 2-3 agents. Only add more for genuinely parallel work.
9. **Fail fast.** Report blockers immediately. Don't retry silently.
10. **Clean up.** Shut down teammates and delete team resources when done.

### Gemini CLI

**Always pass an explicit `model` argument.** Do not rely on defaults. On quota exhaustion, fall down the tier list. `gemini-3.0-flash` is the universal safety net.

```text
mcp__gemini-cli__ask-gemini(prompt="...", model="gemini-3.0-flash")
```text

Models ranked by capability: `gemini-3.1-pro-preview` (preview, high quota risk) → `gemini-3.0-pro` (flagship, high) → `gemini-3.0-flash` (workhorse, low) → `gemini-2.5-pro` (previous gen, moderate) → `gemini-2.5-flash` (bulk, low) → `gemini-2.5-flash-lite` (simple extraction, minimal).

### Agents

Custom agents in `.claude/agents/` — each file defines its role, tools, model, and Gemini fallback chain.

| Agent | Claude Model | Role | Edits Code |
|-------|-------------|------|:---:|
| `researcher` | `opus` | Deep research, API docs, library comparisons | No |
| `architect` | `opus` | System design, tradeoff analysis, design docs | No |
| `engineer` | `opus` | Write code, run tests, fix bugs, commit | Yes |
| `reviewer` | `sonnet` | Code review, quality audit, security review | No |
| `planner` | `sonnet` | Scope estimation, task breakdown, dependencies | No |
| `analyst` | `sonnet` | Data extraction, summarization, SQL queries | No |

### Team Compositions

| Task | Team Name | Teammates | Flow |
|------|-----------|-----------|------|
| Feature implementation | `feature` | researcher, engineer, reviewer | researcher finds docs → engineer implements → reviewer audits diff |
| Architecture / design | `design` | researcher, architect, planner | researcher explores alternatives → architect designs → planner writes stories |
| Code review | `review-PR-NNN` | reviewer, researcher | both review in parallel → lead merges findings |
| Bug investigation | `fix` | researcher, engineer | both investigate in parallel → engineer applies fix |
| Sprint planning | `sprint-planning` | planner, analyst | analyst summarizes state → planner writes and links stories |

### Context Management

Summarize context before spawning teams. Spawn prompts are self-contained (teammates don't inherit conversation). After each phase, persist decisions to files. See `docs/designs/orchestration.md` § Context Management.

1. **Create team** → **Spawn teammates** → **Create tasks** with dependencies
2. **Teammates work** — claim tasks, complete them, report back
3. **Lead synthesizes** — merge findings, approve next steps
4. **Shutdown** — `SendMessage` shutdown to each teammate, then `TeamDelete`
