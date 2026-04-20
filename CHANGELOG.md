# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.3] - 2026-04-20

### Fixed

- `ai sync push` no longer creates new git blobs for unchanged files — SHA-256 check before `shutil.copy2` skips files with identical content, eliminating the primary driver of staging repo pack bloat.
- `git gc --auto` runs after each sync commit to repack loose objects when needed.
- Memory directory non-markdown files (e.g. `.consolidate-lock`) are now excluded from sync via `should_sync_file`, preventing spurious conflict detections and `.conflict.conflict` cascades.

## [0.4.2] - 2026-04-20

### Fixed

- iTerm2 Session Name not updating for local CC sessions (`ai c`). Added `_set_iterm2_name_applescript` which targets the iTerm2 session directly by GUID via the AppleScript API, bypassing DCS passthrough failures caused by nested tmux, process-tracking title overrides, and double-wrap requirements.

## [0.4.1] - 2026-04-19

### Added

- Session auto-refresh on update: when the `ai c` session loop detects a version change after `ai update`, the bash template is reloaded via `ai internal refresh-template` and exec-replaced in the same shell. No user action required; mosh-safe. (AI-CLI-39)
- `ai sync` subcommands: `push`, `pull`, `conflicts`, `watch` — each exposed as a Click subcommand with `--help` at the subcommand level. (AI-CLI-47)

### Fixed

- `_auto_update_if_stale` now uses an `O_CREAT|O_EXCL` lockfile instead of a stamp-file read/write race. Prevents double-update on concurrent session launches.
- `transport.py` deferred import `from .main import _ensure_circusd` was left over after the module split; updated to `from .process_manager import _ensure_circusd`. Caused a test regression where the function was imported before the patch was applied.
- Pyright type error: `entry.value` (`bytes | None`) in `quota.py` now guarded with `if entry.value is not None` before `json.loads`.
- Architecture doc updated: monolithic `main.py` dispatch → Click command group dispatch; `ai tunnel open|close` → `start|stop`; handoff publisher attribution corrected to `handoff.py`.
- Integration tests for `_do_session_launch` now use libtmux directly for `has-session` / `new-session` instead of subprocess rerouting, fixing failures in non-interactive CI environments.

### Refactored

- `main.py` split into 8 modules (`config`, `handoff`, `iterm2`, `layout`, `process_manager`, `session`, `session_script`, `transport`); `main.py` reduced from ~4 000 to ~1 900 lines. All patch targets in tests updated to owning modules. (AI-CLI-39, AI-CLI-47)
- NATS callback closures extracted to module-level functions (`_on_handoff_signal_watch`, `_write_pending_if_claimed_drain`, `_on_quota_snapshot_handler`) so they can be unit-tested without a live NATS server. (AI-CLI-17)

## [0.4.0] - 2026-04-17

### Added

- `ai cc-usage push [-d/--dry-run]`: scan `~/.claude/projects/` JSONL files and push per-call token usage events (input, cache-creation, cache-read, output tokens) to a configured REST API backend. Cursor-tracked — only events since the last successful push are sent, in batches of 500. Config: `[humanware] api_url` and `api_key` in `config.toml`. (AI-CLI-23)
- `ai cc-usage status`: print the number of CC sessions tracked by the push cursor and the timestamp of the last successful push.

### Fixed

- `ai --version` / `ai -V`: no longer raises `PackageNotFoundError` when run in an environment where the package is not installed (e.g. editable dev installs). Falls back to printing `unknown`.
- Session resume (bash template): bare `except: pass` in the Python one-liner that finds the most recent CC session file was catching `SystemExit`, causing `sys.exit(0)` to be swallowed after the first match. All subsequent files sharing the same `customTitle` (e.g. sync-generated `conflict-*.jsonl` copies) also had their paths written to stdout, producing a concatenated multi-path blob in `$matched_file`. `touch` then failed with "File name too long" before CC could launch. Fixed by using `except Exception: pass` so `SystemExit` propagates correctly.
- Session resume (bash template): `ls "$cc_project_dir"/*.jsonl &>/dev/null` used a shell glob that zsh NOMATCH fires on before the command runs, bypassing `&>/dev/null` and printing an error to the terminal. Replaced with a `find`-based check that avoids shell glob expansion entirely.
- `ai statusline` quota indicator — disappeared on remote sessions when the weekly quota reset occurred and the anchor file was not yet refreshed by a scrape. The inline week-start computation now advances forward from a stale anchor (matching `quota_db._get_current_week_start()`) instead of always subtracting one week, which queried the wrong week and returned no rows. (bug)

## [0.3.0] - 2026-04-11

### Added

- `ai spend gemini`: print Gemini usage summary combining local JSONL logs (OAuth/free-tier run counts, per-model stats) with GCP BigQuery billing export (actual billed amounts for paid runs). Graceful degradation when BigQuery is not configured or `google-cloud-bigquery` not installed. Config under `[gemini_billing]` in `config.toml`. (AI-CLI-41 T-03)
- `ai gemini -P`/`--confirm-paid`: explicit per-run confirmation flag for Deep Research paid API usage. Required when `paid_fallback_enabled = true` in config; exits with actionable error if absent. (AI-CLI-41 T-02)
- `ai gemini` — `paid_fallback_enabled` config key under `[gemini]` in `config.toml`: when `false` (default), the `ai_studio_paid` tier is excluded from all fallback chains, preventing accidental paid API spend. Set `true` only after confirming billing credit status. (AI-CLI-41 T-02)
- `ai gemini` — Deep Research daily run counter persisted to `~/.local/state/ai-cli/dr-daily.json`. Paid run count printed to stderr after each successful run; warning printed when approaching soft daily limit (`DEEP_RESEARCH_DAILY_WARNING = 18` out of `DEEP_RESEARCH_DAILY_LIMIT = 20`). (AI-CLI-41 T-02)

### Changed

- `ai gemini` — auth tier names are now Google-aligned: `oauth` (was `gemini-cli (OAuth)`), `ai_studio_free` (was `API free-tier`), `ai_studio_paid` (was `API paid tier-1`). Used in JSONL logs and fallback-chain messages. (AI-CLI-41 T-01)
- `ai gemini` — token counts (`input_tokens`, `output_tokens`, `total_tokens`) in JSONL logs are now `null` (not `0`) when usage metadata is absent from the API response. Null is distinguishable from a model that genuinely returned zero tokens. (AI-CLI-41 T-01)
- `ai gemini` — `GeminiResult` gains `is_deep_research: bool` field (logged to JSONL); token fields changed from `int = 0` to `int | None = None`. (AI-CLI-41 T-01)

### Fixed

- `ai sync`: removed handoff queue sync and config file sync from push/pull pipeline. Scope is now explicitly CC session data only: `~/.claude/projects/` JSONL and memory files, `~/.claude/history.jsonl`. Git-tracked files (config, hooks, statusline script) are no longer touched by `ai sync`.
- `ai update`: now deploys `src/ai_cli/data/statusline-command.sh` to `~/.claude/statusline-command.sh` as a plain file, replacing any existing symlink. The statusline script is now owned by this package, not by any other project repo.
- `ai signal-watch start`: removed invalid `autostart` key from Circus `add` options — was silently failing watcher registration so signal-watch was never actually started via Circus when `ai c` launched. `start=True` top-level parameter already handles immediate start. (AI-CLI-16)
- `ai sync pull`: removed `replicate_history_to_worktrees` from pull pipeline — it was injecting phantom `history.jsonl` entries (worktree-path copies of main-project entries) on every pull. These phantoms caused the conversation picker to show the wrong project's sessions inside worktree sessions, and made `--continue` fail to find the correct conversation. Added `purge_phantom_history_entries()` which runs on each pull to clean up any previously created phantoms. Existing phantom entries are purged on the next `ai sync pull`.
- `ai ps cron`: VPN detection in `cmd_ps` — switches to `vpn_host` when Mullvad is active, preventing a 30s hang at session start when the Tailscale IP is unreachable. Added `ConnectTimeout=5` to the remote SSH call. (AI-CLI-37)
- `ai internal handoff-drain` / NATS tunnel: `NATSClient._open_ssh_tunnel()` now checks only `AI_CLI_HOST` (was silently skipping when the var was absent from non-login shells). Session start no longer hangs on NATS retry loop on Mac when NATS tunnel fails to open. (AI-CLI-37)
- `ai tunnel start`: `_cmd_tunnel_start` now uses `vpn_host` when VPN is active so autossh reaches the server through the VPN-routed address rather than the Tailscale IP. (AI-CLI-37)
- Remote sessions / tmux invocations: replaced all `bash -l -c` / `bash -c` with `zsh -l -c` / `zsh -c` so that `~/.zshenv` (the canonical env var file) is sourced on remote connects and all tmux session spawns. (AI-CLI-37)

### Changed

- `AI_CLI_HOST` replaces `HUMANWARE_HOST` as the canonical env var for host machine identification (`mac`, `hetzner`, etc.). `AI_CLI_HOST` is the public/open-source name; `HUMANWARE_HOST` is no longer referenced in the codebase. Set `AI_CLI_HOST` in `~/.zshenv` (sourced by all zsh sessions including non-interactive). (AI-CLI-37)

## [0.2.0] - 2026-04-06

### Added

- `ai gemini -m deep-research`: Gemini Deep Research via the Interactions API (`deep-research-pro-preview-12-2025`). Submits a background job, polls every 30s until complete, cancels via DELETE on Ctrl-C. Auth: `GOOGLE_API_KEY_FREE_TIER` → `GOOGLE_API_KEY_TIER_1` (REST-only; no OAuth path). Output follows the same `-o`/auto-file/stdout conventions as other models. (AI-CLI-36)
- `ai --version` / `ai -V`: print the installed package version and exit.
- `ai gemini -s`/`--start-tier TIER`: Skip earlier auth tiers explicitly (1=OAuth CLI, 2=free API key, 3=paid API key). Useful when OAuth returns truncated responses without erroring. (AI-CLI-36)
- `ai gemini -d standard`/`--depth standard`: Planner-Executor research pipeline — query generation → concurrent Gemini-grounded search → synthesis. Per-step JSON checkpointing at `~/.local/state/ai-cli/research-runs/<run-id>/`. Resume with `--resume <run-id>`. (AI-CLI-36)

### Fixed

- `ai gemini -m deep-research`: Interactions API submit response returns `"id"` (flat string), not `"name"` (resource path). Code was using `.get("name", "").split("/")[-1]`, producing an empty string and silently failing before polling started. Fixed to `interaction.get("id") or interaction.get("name", "").split("/")[-1]`. (AI-CLI-36)
- Prompt injection watcher: hardened against stale `❯` prompt visible during `claude --continue` startup. Added 10-cycle grace period, double `capture-pane` verification, removed `C-u` keystroke, and signal file is now deleted after injection (not before). (AI-CLI-35)
- Process hygiene (`ai ps`): stale PID file cleanup, orphan detection, and session health checks.
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
- `ai setup` command: detects managed platform vs standalone environment and configures `CLAUDE.md` accordingly; marks file `assume-unchanged` in git after swap so external users don't see local modifications
- `CLAUDE-full.md`: standalone self-contained Claude Code session config for users without the managed platform; `CLAUDE.md` remains the lean variant for managed platform users
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

[Unreleased]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/sergeiwallace/ai-cli-utils/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sergeiwallace/ai-cli-utils/releases/tag/v0.1.0
