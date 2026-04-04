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

- **Dev branch:** `main` — feature branches merged via PR

## Public Open-Source Package Standards

This is a **public open-source package**. All code, docs, comments, tests, and commit messages must be written for a general audience.

- **No proprietary names** — never reference humanware, aido, or any private platform/tool names in code, docs, comments, or tests
- **No personal identifiers** — no personal names (first or last), usernames, private server IPs/hostnames, or account-specific paths. Use generic placeholders: `user`, `myproject`, `example.com`, `192.0.2.x`
- **No private project names or prefixes** — don't hardcode any project names or session prefixes from personal workflow in source, docs, or tests. Use only fully generic names: `myproject`, `myapp`, `session-1`, `test-session`
- **Generic examples throughout** — all session names, project names, and config values in tests/docs must be obviously placeholder
- **OS portability** — all code must account for Windows, macOS, and Linux differences. No macOS-only assumptions. Use `sys.platform`, `pathlib`, and `os` abstractions
- **Commit messages are public** — same rules apply to git commit messages

## CLI Conventions

- **All options must have both short and long forms** — e.g. `-f`/`--force`, `-d`/`--dry-run`. No long-only flags (except hidden internal `SUPPRESS` flags passed machine-to-machine)

## Test Requirements

- **`# pragma: no cover` is a hard human gate** — never add it without explicit user approval. If a line cannot be covered, document it with the specific line, why it can't be mocked, and options. Wait for explicit approval.

## Your Role

- Research topics in parallel with Claude — results are synthesized
- Review code quality while Claude reviews architecture — reports are merged
- For full project conventions and implementation workflow, see `CLAUDE.md`
