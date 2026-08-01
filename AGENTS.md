# Lean configuration

## Public Open-Source Package Standards

This is a **public open-source package**. All code, docs, comments, tests, and commit messages must be written for a general audience.

- **No proprietary names** — never reference ai-core, aido, or private platform/tool names in code, docs, comments, or tests.
- **No personal identifiers** — no personal names (first or last), usernames, private server IPs/hostnames, or account-specific paths. Use generic placeholders: `user`, `myproject`, `example.com`, `192.0.2.x`.
- **No private project names or prefixes** — do not hardcode personal-workflow project names or session prefixes in source, docs, or tests. Use only generic names such as `myproject`, `myapp`, `session-1`, and `test-session`.
- **Generic examples throughout** — all session names, project names, and configuration values in tests/docs must be obvious placeholders, not possible real personal-workflow artifacts.
- **OS portability** — account for Windows, macOS, and Linux differences. Do not assume macOS-only locations or tools (for example `~/Library/`, `pbcopy`, or `open`). Use `sys.platform`, `pathlib`, and `os` abstractions. Flag unavoidable platform-specific code with a comment.
- **Commit messages are public** — the same rules apply to git commit messages.
- If an existing violation is found while working, flag it immediately rather than letting it accumulate.

## CLI Conventions

- **All options must have both short and long forms** — for example `-f`/`--force`, `-d`/`--dry-run`. Do not add long-only flags, except hidden internal `SUPPRESS` flags passed machine-to-machine.
- When adding a new CLI option, add both forms simultaneously. Do not ship long-only flags.

## Test Requirements

### Python

- Test names: `test_{given}_{when}_{then}`
- Use pytest fixtures for shared setup.
- A reviewer role audits test quality on every review; see `.claude/agents/reviewer.md`.
- Run: `pytest`
- **`# pragma: no cover` is a hard human gate** — never add it autonomously. If a line cannot be covered, document the specific line, why it cannot be mocked, and options in the UAT report. Wait for explicit user approval before adding a pragma.
