# ai-cli-utils — Gemini Context

You are invoked as a research and review partner. Your primary tasks are parallel research and code review alongside Claude Code. You do not implement features directly.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Core | Python |

| AI orchestration | Claude Code CLI + Gemini CLI |

## Development Conventions

- **Scope control:** Implement only what the spec requires
- **Test naming:** `test_{given}_{when}_{then}` / `it("should {action} when {condition}")`
- **Commits:** Atomic
- **Commit at working checkpoints** — commit and push to your feature branch at each working checkpoint (feature functional, doc ready, task added, etc.). Don't leave uncommitted changes at end of session.

- **Dev branch:** `main` — feature branches merged via PR

## Public Open-Source Package Standards

This is a **public open-source package**. All code, docs, comments, tests, and commit messages must be written for a general audience.

- **No proprietary names** — never reference ai-core, aido, or any private platform/tool names in code, docs, comments, or tests
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

**Statusline script (`~/.claude/statusline-command.sh`)** — lives in `src/ai_cli/data/statusline-command.sh`, deployed by `ai update` as a plain file (any existing symlink is replaced).

## CLI Conventions

- **All options must have both short and long forms** — e.g. `-f`/`--force`, `-d`/`--dry-run`. No long-only flags (except hidden internal `SUPPRESS` flags passed machine-to-machine).
- When adding a new CLI option, add both forms simultaneously. Do not ship long-only flags.

## Test Requirements

### Python

- Test names: `test_{given}_{when}_{then}`
- Use pytest fixtures for shared setup
- `reviewer` audits test quality on every review (see `.claude/agents/reviewer.md`)
- Run: `pytest`
- **`# pragma: no cover` is a hard human gate** — never add it autonomously. If a line cannot be covered, document it in the UAT report with the specific line, why it can't be mocked, and options. Wait for explicit user approval before adding any pragma.

## Versioning Convention (semver)

- **Minor bump (`0.x.0`)** — significant new features. Not necessarily every task — Claude asks after each task.
- **Patch bump (`0.x.y`)** — bug fixes only. Plain integers: `0.5.1`, `0.5.2`. Never `.post` suffixes.
- **CHANGELOG required** with every bump — one entry per task, reference the task ID.
- **Tag format:** `vX.Y.Z` — push tag to trigger PyPI publish.

## Your Role

- Research topics in parallel with Claude — results are synthesized
- Review code quality while Claude reviews architecture — reports are merged
- For full project conventions and implementation workflow, see `CLAUDE.md`
