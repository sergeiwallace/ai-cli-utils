# lean config

## Public Open-Source Package Standards

This is a **public open-source package**. All code, docs, comments, tests, and commit messages must be written for a general audience.

- **No proprietary names** — never reference ai-core, aido, or any private platform/tool names in code, docs, comments, or tests
- **No personal identifiers** — no personal names (first or last), usernames, private server IPs/hostnames, or account-specific paths. Use generic placeholders: `user`, `myproject`, `example.com`, `192.0.2.x`
- **No private project names or prefixes** — don't hardcode any project names or session prefixes from personal workflow in source, docs, or tests. Use only fully generic names: `myproject`, `myapp`, `session-1`, `test-session`
- **Generic examples throughout** — all session names, project names, and config values in tests/docs must be obviously placeholder. Nothing that could be mistaken for a real personal workflow artifact.
- **OS portability** — all code must account for Windows, macOS, and Linux differences. No macOS-only assumptions (e.g. `~/Library/`, `pbcopy`, `open`). Use `sys.platform`, `pathlib`, and `os` abstractions. Flag any unavoidably platform-specific code with a comment.
- **Commit messages are public** — same rules apply to git commit messages
- If you catch an existing violation while working, flag it immediately rather than letting it accumulate

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
