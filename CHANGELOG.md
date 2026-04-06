# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ai gemini -m deep-research`: Gemini Deep Research via the Interactions API (`deep-research-pro-preview-12-2025`). Submits a background job, polls every 30s until complete, cancels via DELETE on Ctrl-C. Auth: `GOOGLE_API_KEY_FREE_TIER` → `GOOGLE_API_KEY_TIER_1` (REST-only; no OAuth path). Output follows the same `-o`/auto-file/stdout conventions as other models. (AI-CLI-36)
- `ai gemini -s`/`--start-tier TIER`: Skip earlier auth tiers explicitly (1=OAuth CLI, 2=free API key, 3=paid API key). Useful when OAuth returns truncated responses without erroring. (AI-CLI-36)
- `ai gemini -d standard`/`--depth standard`: Planner-Executor research pipeline — query generation → concurrent Gemini-grounded search → synthesis. Per-step JSON checkpointing at `~/.local/state/ai-cli/research-runs/<run-id>/`. Resume with `--resume <run-id>`. (AI-CLI-36)

### Fixed

- `ai gemini -m deep-research`: Interactions API submit response returns `"id"` (flat string), not `"name"` (resource path). Code was using `.get("name", "").split("/")[-1]`, producing an empty string and silently failing before polling started. Fixed to `interaction.get("id") or interaction.get("name", "").split("/")[-1]`. (AI-CLI-36)

### Added

- `ai layout` command: YAML-driven iTerm2 window/tab/pane layout system. Subcommands: `list`, `validate <name>`, `profiles <name>`, `<name>` (apply). Layout files at `~/.config/iterm2/layouts/*.yaml`. Nested pane split model (vertical/horizontal). Dynamic Profile generation + runtime tinted icon per tab. See `docs/designs/iterm2-layout-system.md`.
- `icon_generator` module: runtime PNG icon generation with Pillow. Auto-contrast tint derived from tab background color via HSL color theory (180° hue rotation + lightness adaptation). Explicit `icon_color` override supported. Source logos at `src/ai_cli/data/icons/`. Falls back to Claude brand orange (`#da7756`) when no tab color is set.
- Per-session Dynamic Profile generation: each session gets a `ai-cli:{ai_name}` Dynamic Profile JSON (inherits from base profile, sets tab color + icon) written to `~/Library/Application Support/iTerm2/DynamicProfiles/ai-cli-generated/` at launch and cleaned up on exit.
- Lease-file-based collision-free iTerm2 tab color assignment. Replaces modulo-based system; any palette size supported. Lease files at `~/.local/state/ai-cli/iterm2/color-leases.json`.
- `[iterm2.base_profiles]` config section: configure which iTerm2 base profile each session type inherits from.
- `[iterm2.project_colors]` config section: pin project/session names to preferred palette color slots.
- `[iterm2.icon_color_overrides]` config section: explicit icon tint override per palette color slot.
- `ai color <name|#hex>` command: ad hoc tab color reassignment for the current session.
- `ai internal cleanup-session-files <ai_name>`: remove session icon PNG and Dynamic Profile JSON; called by EXIT trap.
- OSC 1 title fix: session name now set via `\033]1;` instead of `\033]0;` so mosh on remote sessions does not prepend `[mosh] ` to the tab title.
- `ai setup` command: detects humanware platform vs standalone environment and configures `CLAUDE.md` accordingly; marks file `assume-unchanged` in git after swap so external users don't see local modifications
- `CLAUDE-full.md`: standalone self-contained Claude Code session config for users without the humanware platform; `CLAUDE.md` remains the lean variant for humanware users
- pyright basic mode type checking in CI lint job

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
