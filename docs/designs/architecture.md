---
title: ai-cli-utils — Architecture
category: design
tags: [architecture, cli, quota, sync, nats, circus]
status: active
source: claude-sonnet-4-6 2026-04-18
template_version: "design-1.0.0"
---
<!-- doc:region name="overview" kind="replaceable" -->

# ai-cli-utils — Architecture

<!-- COMP-128: the ToC sits ABOVE the Executive Summary (it is self-referential otherwise).
  D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc, with GitHub-style
  anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate in-window
  (incl. VS Code Remote-SSH). `companion toc check` validates this once COMP-127 lands. If
  all-`###` proves too noisy, fall back to D5 (a) "meaningful `###`" — a deterministic
  OR-rule: include a `###` when it (1) has child `####`, (2) its section body ≥ ~8-10
  lines, (3) its parent `##` is allowlisted (Design Decisions / Open Questions /
  appendices), or (4) matches a pattern (`### D-N`); `<!-- toc:skip -->` /
  `<!-- toc:include -->` on a heading override the heuristic. -->

## Table of Contents

- [Overview](#overview)
- [Module Map](#module-map)
- [Command Groups](#command-groups)
- [Key Data Types](#key-data-types)
- [External Integrations](#external-integrations)
- [Key Design Decisions](#key-design-decisions)
- [Dependencies](#dependencies)

---

## Overview

`ai-cli-utils` is a session manager and automation toolkit for Claude Code, Gemini CLI, Pi, and Codex. It wraps those tools in tmux sessions with numbered sessions, git worktree isolation, mosh/SSH remote access, cross-machine memory sync, and session lifecycle management.

The tool installs as a single `ai` command. There is no server component — all state is local SQLite, local files, and optionally NATS for cross-machine events.

---

## Module Map

| File | Description |
|------|-------------|
| `main.py` | CLI entrypoint (`ai` command); command dispatch, session-launch plumbing, update/deploy helpers. Thin after AI-CLI-39 refactor — most subsystems now live in dedicated modules below |
| `config.py` | XDG path helpers, `load_config`, session map read/write, project registry (loaded from `myproject.toml`) |
| `direnv_setup.py` | Portable checks and approval helpers for project `.envrc` files |
| `git_repair.py` | Detects and repairs missing tracked symlinks, phantom deletions, and bare-worktree configuration |
| `session.py` | Session naming (`c-myproject-1` etc.), worktree creation/cleanup, Gemini UUID/checkpoint lookup |
| `iterm2.py` | iTerm2 color-slot leases, profile emit escape sequences, tmux `allow-passthrough` config |
| `transport.py` | VPN-aware mosh/SSH loop for `ai c -R`; auto-starts Tailscale on Mac when host unreachable |
| `tunnel.py` | autossh SSH tunnels (`ai tunnel`) and CDP/Chrome debug server (`ai cdp`) |
| `process_manager.py` | Circus daemon bootstrap and quota-watch lifecycle |
| `process_probe.py` | Per-OS process inspection and termination behind one interface (`ProcessProbe`, resolved by `probe_for`): presence, state, start-time identity, and a bounded termination escalation. `ProcfsProbe` reads Linux `/proc`; `PsutilProbe` covers macOS and Windows. Backs the session-registry liveness check and abandoned-session reclamation, which were Linux-only before it |
| `session_script.py` | `get_engine_script` — bash template that wraps each session's engine loop; its in-shell watcher handles exit signals, config-change restarts, and Gemini reload/restart signals |
| `quota.py` | Claude quota scraper and watcher; polls `/usage` via hidden tmux window; publishes NATS threshold events and `hw.events.usage.claude.snapshot`; stores snapshots in SQLite and NATS KV; statusline reads KV first, falls back to SQLite; threshold alerts delivered via `Notifier` |
| `quota_db.py` | SQLite persistence for quota tracking (`~/.local/state/ai-cli/quota.db`); stores usage records, snapshots, weekly reset anchors, and `notification_log` (full delivery history with per-channel success/failure) |
| `cc_usage.py` | CC JSONL scanner for per-call token data; cursor-tracked incremental push to core-cli REST API; defines `CCTokenEvent`, `PushResult` |
| `messaging.py` | Async NATS client wrapper with JetStream; SSH tunnel auto-open on Mac when port 4222 is unreachable; defines `NATSClient`, stream configs, heartbeat/event helpers |
| `sync.py` | Bidirectional CC session data sync (conversation JSONL + memory files) via bare git staging repo over SSH; defines `SyncConfig` |
| `telemetry.py` | Event recording pipeline: caller → NATS JetStream → background writer → SQLite (`~/.ai-cli/telemetry.db`) |
| `spend.py` | Historical Gemini CLI cost reporting from local run logs, with optional GCP BigQuery billing export |
| `memory.py` | inotify/FSEvents-based memory file watcher; debounces MEMORY.md writes; publishes `memory.dream.*` NATS events |
| `notifications.py` | Unified notification delivery: `Notifier` class fires all configured channels in parallel (Discord webhook, ntfy push, OS native); `NotificationResult` per-channel result tracking; OS fallback fires when all primaries fail; `NotificationManager` for iTerm2 badge updates via OSC escape sequences |
| `vpn_watch.py` | Circus-managed daemon; polls VPN state; publishes `vpn.state.changed` to NATS |
| `process_hygiene.py` | Orphaned process detection and cleanup for mosh-server, session watchers, autossh, circusd, and nats-server |
| `layout.py` | iTerm2 YAML layout templating; generates Dynamic Profiles; builds windows/panes via iTerm2 Python API |
| `icon_generator.py` | Runtime iTerm2 icon tinting via Pillow; computes complementary HSL tint from tab color |
| `copier_update.py` | Runs `copier update` across all `project-template`-based projects; scans for conflict markers |
| `setup.py` | Detects managed vs. standalone environment; switches `CLAUDE.md` variant accordingly |
| `cc_migrate.py` | Safely moves a Claude Code transcript between project roots |
| `session_adopt.py` | Adopts an externally started Claude Code session into a managed session slot |
| `session_audit.py` | Surveys titled Claude Code sessions and identifies sessions that cannot be resumed by `ai c` |
| `stale_session_reaper.py` | Fail-closed stale tmux session evaluation and explicit reaping |
| `trust.py` | Claude Code workspace-trust registration and backfill |
| `workspace.py` | Workspace-wide git pull/rebase operations across repositories and worktrees |
| `data/statusline-command.sh` | Shell script deployed by `ai update`; called by the iTerm2 statusline |

---

## Command Groups

### Session Management
- `ai c [N] [-p PROJECT] [-R]` — launch or resume Claude Code tmux session with git worktree isolation
- `ai g [N] [-p PROJECT]` — launch or resume Gemini CLI tmux session
- `ai p [N] [-p PROJECT]` — launch or resume a Pi tmux session
- `ai cx [N] [-p PROJECT]` — launch or resume a Codex tmux session
- `ai ls [-a]` — fzf-powered session picker sorted by activity
- `ai attach SESSION` — attach to named tmux session
- `ai reconnect [N ...]` — print reconnect commands for matching remote sessions
- `ai cc-migrate DEST` — move a Claude Code transcript to another project root
- `ai session-adopt [NAME]` — adopt a session that was started outside `ai c`
- `ai session-audit` — survey titled sessions and identify sessions that cannot be resumed

### Historical Gemini Spend
- `ai spend gemini` — report historical Gemini CLI run logs and optional BigQuery billing data; it does not invoke Gemini

> **Removed in v0.7.0:** `ai gemini` and the `gemini.py` and `research.py` modules were removed. The historical `ai spend gemini` command remains only to report logs created before that removal.

### Quota Tracking
- `ai quota watch start|stop|status` — manage quota-watch Circus daemon lifecycle
- `ai quota watch run [-i/--interval]` — raw daemon entry point (Circus uses this)
- `ai quota scrape` — one-shot quota scrape, store, and publish
- `ai quota status` — read current quota from SQLite (with KV sync if stale)

### Notifications
- `ai notifications list` — audit configured channels (name, enabled, credentials present/absent)
- `ai notifications log [-n N] [-s SINCE] [-f FROM] [-t TO] [--source TEXT] [--failed]` — query notification delivery history

### CC Token Tracking
- `ai cc-usage push` — scan CC JSONL files, push new token events to core-cli REST API
- `ai cc-usage status` — show cursor state and last-push summary

### Sync
- `ai sync push/pull/conflicts/watch [-m/-f]` — sync CC session data between machines via bare git repo
- `ai ws pull` — pull/rebase the repositories and worktrees in a workspace file

### Daemons / Process Management
- `ai ps [clean]` — inspect and clean up stale ai-cli processes and PID files
- `ai vpn-watch` — Circus-managed VPN state daemon
- `ai memory watch` — start memory file watcher
- `ai telemetry writer` — start NATS-to-SQLite telemetry writer

### Infrastructure / iTerm2
- `ai layout NAME|list|validate|profiles` — YAML-driven window/tab/pane definitions
- `ai color COLOR` — set session tab color and icon tint
- `ai tunnel start|stop|status` — persistent SSH tunnels via autossh
- `ai cdp start|stop|status` — Chrome DevTools Protocol debug server

### Maintenance / Setup
- `ai update` / `ai deploy` — reinstall package in all configured venvs + deploy statusline script
- `ai upgrade` — pull latest from git and redeploy
- `ai setup` — configure CLAUDE.md variant for the current environment
- `ai doctor [-d]` — check required native binaries and install supported missing tools
- `ai register PROJECT [-p PREFIX] [-t TYPE]` — register a repository root and task prefix
- `ai copier-update [--project NAME]` — propagate project-template changes to all downstream projects
- `ai ssh [ALIAS]` — open an SSH shell to a configured remote machine
- `ai trust-backfill [-r ROOT]` — register Claude Code workspace trust for repositories under a root
- `ai internal publish SUBJECT PAYLOAD` — raw NATS publish (used by hooks)

---

## Key Data Types

| Type | Module | Key Fields |
|------|--------|------------|
| `QuotaSnapshot` | `quota.py` | `weekly_all_models_pct`, `session_pct`, `weekly_sonnet_pct`, `extra_pct`, `reset_at` |
| `CCTokenEvent` | `cc_usage.py` | `id`, `session_id`, `project_path`, `machine`, `model`, `input_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `output_tokens`, `occurred_at` |
| `PushResult` | `cc_usage.py` | Push outcome summary (event count, errors) |
| `SyncConfig` | `sync.py` | `staging_dir`, `remote_url`, `local_prefix`, `remote_host`, `source_machine` |

---

## External Integrations

### NATS JetStream

| Subject | Publisher | Description |
|---------|-----------|-------------|
| `fleet.worker.{session_id}.heartbeat` | `messaging.py` | Per-session heartbeat |
| `quota.threshold.{50\|75\|90}` | `quota.py` | Claude quota threshold alerts |
| `quota.snapshot` | `quota.py` | Legacy cross-machine quota sync |
| `hw.events.usage.claude.snapshot` | `quota.py` | Claude quota snapshot (core-cli ingest) |
| `memory.dream.started/completed` | `memory.py` | Memory consolidation lifecycle |

**NATS KV bucket `hw_state`:**
- `quota.claude.current` — latest `QuotaSnapshot` JSON; written by `quota.py`, read by statusline and `ai quota status`

### REST API (optional)
- `POST /api/v1/usage/cc/ingest` — CC token events from `cc_usage.py`
- `GET /api/v1/usage/claude/current` — read Claude quota tier (used by companion)

### SQLite Databases
- `~/.local/state/ai-cli/quota.db` — quota snapshots, usage records, weekly state
- `~/.ai-cli/telemetry.db` — telemetry events with subject/machine/session index

### External Processes / Services
- **tmux** (via `libtmux`) — session creation, attachment, hidden quota-scrape windows
- **Circus** — process supervisor for the quota-watch and VPN-watch daemons
- **SSH/mosh** — remote session launch; SSH tunnels for NATS access on Mac
- **Gemini CLI** (`gemini` binary) — launches Gemini tmux sessions through `ai g`
- **GCP BigQuery** (optional) — historical billing data for `ai spend gemini`
- **watchdog** — inotify/FSEvents for memory file watching and sync watch
- **fzf** — interactive session picker in `ai ls`
- **copier** — project template propagation in `ai copier-update`

### Files on Disk
- `~/.claude/projects/` — CC session JSONL files (scanned by `cc_usage.py`, synced by `sync.py`)
- `~/.local/state/ai-cli/gemini-logs/YYYY-MM-DD.jsonl` — historical Gemini run logs, if retained from a version before v0.7.0
- `~/.local/state/ai-cli-utils/cc-usage-cursor.json` — per-session JSONL scan cursors

---

## Key Design Decisions

**Removed Gemini API wrapper** — v0.7.0 removed `ai gemini`, `gemini.py`, and `research.py`. `ai g` still launches an installed Gemini CLI in a tmux session; `ai spend gemini` only reports pre-removal local logs.

**Dual-path quota state** — `quota.py` writes each snapshot to both NATS KV (`quota.claude.current`) and local SQLite. The statusline reads KV first with a 300ms thread timeout, falls back to SQLite. This keeps Mac and Hetzner statuslines aligned without requiring a local scrape on every machine.

**Git worktree isolation** — each `ai c N` session runs in `.worktrees/sw-N/` on branch `wt-sw-N`, **created at, and tracking, the repository's integration branch**. That branch resolves from the `[worktree_upstream]` config table if the repository declares one, otherwise from the branch the repository's own main checkout is on — so a repository on `main` is created at and tracks `origin/main`, unchanged. Created and destroyed by `main.py`.

Base and upstream are resolved together, from one call, so they can never disagree. Three properties are deliberate:

- The base is the *remote-tracking* ref of that branch, never the local tip. `git worktree add -b` defaults its start-point to the main tree's current HEAD, which silently produces a branch that is not review-clean (BUG-004).
- Neither base nor upstream is hardcoded to `main`, and no repository name is special-cased in source. Pointing every worktree at `origin/main` aims routine session work at the branch a pull-request-workflow repository forbids pushing to.
- Where the branch cannot be resolved on `origin`, the worktree gets **no upstream** plus a warning — never a fallback to `origin/main`. A missing upstream makes `git push` stop and ask. A branch that exists nowhere, a detached HEAD, or a missing `origin` remote is a hard failure, never a fallback to HEAD.

Note that `git worktree add -b <branch> <remote-tracking-ref>` attaches an upstream *by itself* via git's default `branch.autoSetupMerge`, so it is a second upstream writer in this path: declining to attach one has to actively clear it.

**CC session sync via bare git** — `sync.py` uses a bare git repo as the transport for CC JSONL + memory files between machines. Conflict detection is file-level mtime comparison logged to `~/.claude-sync-conflicts.log`.

**Cursor-based CC token tracking** — `cc_usage.py` tracks last-seen `occurred_at` per session UUID in `cc-usage-cursor.json`. Only entries newer than the cursor are pushed. Idempotent: core-cli ingest deduplicates by `event_id`.

**Click command group dispatch** — command routing uses a `@click.group()` tree. `ai internal` is kept as a pre-Click fast path for bash hook performance (avoids Click startup overhead). Migrated from `if sys.argv[1] == ...` argparse hybrid in AI-CLI-39/47.

**Machine self-awareness via `AI_HOST`** — NATS tunnel auto-open, SSH command routing, and sync source labeling all branch on the `AI_HOST` env var (`mac`, `hetzner`).

**XDG-compliant paths with migration** — config at `$XDG_CONFIG_HOME/ai-cli-utils/`, state at `$XDG_STATE_HOME/ai-cli-utils/`. `_migrate_xdg_dir()` auto-renames old `ai-cli` dirs on first access.

---

## Dependencies

| Package | Role |
|---------|------|
| `nats-py>=2.9.0` | NATS/JetStream messaging |
| `circus>=0.18.0` | Process supervisor for daemon management |
| `libtmux>=0.30.0` | tmux session/window/pane control |
| `watchdog>=4.0.0` | inotify/FSEvents for file watching |
| `pyyaml>=6.0` | Layout YAML parsing |
| `pillow>=10.0` | iTerm2 icon tinting |
| `google-cloud-bigquery` | (optional) historical GCP billing data for `ai spend gemini` |
| `pydantic` | (optional) Layout file schema validation |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="decisions" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="approval_log" -->
