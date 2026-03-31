# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- iTerm2 session-tracking files moved from `/tmp/` to `~/.local/state/ai-cli/iterm2/` (XDG Base Directory Specification), eliminating symlink attack surface on shared servers
- Session runtime files (`cc-exit-*`, `cc-resume-prompt-*`, `ai-watcher-lock-*`) moved from `/tmp/` to `~/.local/state/ai-cli/`
- `session_id_uuid` validated against UUID regex before bash f-string interpolation in `get_engine_script()`; malformed input is cleared
- `--project` argument rejects values containing `/` or `\` path separators

### Added

- CI test matrix (Python 3.11/3.12/3.13) with Codecov coverage reporting
- GitHub Release automation on tag push
- Pre-commit config (ruff + hygiene hooks)
- Renovate for dependency management (replaces Dependabot)
- Issue templates (bug report, feature request) and PR template
- Full README rewrite (PyOpenSci 13-section structure)
- CONTRIBUTING.md

## [0.1.1] - 2026-03-29

### Changed

- Generalized all code to remove hardcoded personal references; package now works
  for any user with config-driven settings
- `_get_main_project_name()` returns `None` instead of a default when not configured
- Sync requires explicit `[sync] remote_host` or `[remote] host` configuration
- Settings path translation is now config-driven instead of hardcoded

### Added

- CI workflow (ruff lint + pytest) on push to main and PRs
- PyPI publish workflow with Trusted Publishers (OIDC)
- Shields.io badges in README (PyPI, Python version, license, CI)
- CHANGELOG.md
- SECURITY.md with vulnerability reporting instructions

### Fixed

- 13 ruff lint issues (unused imports, undefined names, unused variables)
- 7 failing tests (remote transport, sync worktree isolation, PID lock mocking)

## [0.1.0] - 2026-03-29

### Added

- Initial release extracted from monorepo
- Unified `ai` CLI entry point for Claude Code and Gemini CLI sessions
- tmux-based session management with auto-resume loop
- Git worktree isolation per session
- Remote session support via mosh/SSH
- Cross-machine sync (`ai sync push/pull`) with bidirectional git staging
- JSONL conversation sync with cwd path translation
- Handoff queue for cross-session task delegation
- NATS-based fleet messaging (heartbeats, events, sync notifications)
- Desktop and push notifications
- Memory watch daemon with auto-dream support
- Quota tracking for API usage
- Stale session cleanup
- Session reconnection (`ai reconnect`)

[Unreleased]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sergeiwallace/ai-cli-utils/releases/tag/v0.1.0
