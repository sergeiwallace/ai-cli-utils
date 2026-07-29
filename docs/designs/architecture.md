---
title: ai-cli-utils — Architecture
category: design
tags: [architecture, cli, gemini, quota, sync, nats, circus]
status: active
source: claude-sonnet-4-6 2026-04-18
template_version: "design-1.0.0"
---
<!-- doc:region name="overview" kind="replaceable" -->

# ai-cli-utils — Architecture

<!-- AIDO-128: the ToC sits ABOVE the Executive Summary (it is self-referential otherwise).
  D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc, with GitHub-style
  anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate in-window
  (incl. VS Code Remote-SSH). `aido toc check` validates this once AIDO-127 lands. If
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

`ai-cli-utils` is the unified AI session manager and automation toolkit for Claude Code and Gemini CLI. It wraps both tools in tmux sessions with production workflow features: numbered sessions, git worktree isolation, mosh/SSH remote access, cross-machine memory sync, and session lifecycle management. It also provides `ai gemini` — a Gemini CLI wrapper with 3-tier auth fallback (OAuth → free API key → paid API key) that automatically retries on capacity errors.

The tool installs as a single `ai` command. There is no server component — all state is local SQLite, local files, and optionally NATS for cross-machine events.

---

## Module Map

| File | Description |
|------|-------------|
| `main.py` | CLI entrypoint (`ai` command); command dispatch, session-launch plumbing, update/deploy helpers. Thin after AI-CLI-39 refactor — most subsystems now live in dedicated modules below |
| `config.py` | XDG path helpers, `load_config`, session map read/write, project registry (loaded from `myproject.toml`) |
| `session.py` | Session naming (`c-myproject-1` etc.), worktree creation/cleanup, Gemini UUID/checkpoint lookup |
| `iterm2.py` | iTerm2 color-slot leases, profile emit escape sequences, tmux `allow-passthrough` config |
| `handoff.py` | Handoff queue (`post`, `check`, `claim`, `complete`) + signal-watch helper `_claim_handoff_for_signal` |
| `transport.py` | VPN-aware mosh/SSH loop for `ai c -R`; auto-starts Tailscale on Mac when host unreachable |
| `tunnel.py` | autossh SSH tunnels (`ai tunnel`) and CDP/Chrome debug server (`ai cdp`) |
| `process_manager.py` | Circus daemon bootstrap + `signal-watch` process lifecycle |
| `session_script.py` | `get_engine_script` — bash template that wraps each session's engine loop with watcher, handoff drain, iTerm2 status |
| `gemini.py` | Gemini CLI/API wrapper with 3-tier auth fallback; defines `GeminiResult`, `AttemptLog`; handles OAuth, free-key REST, paid-key REST; writes JSONL run logs; publishes `hw.events.usage.gemini.event` to NATS |
| `quota.py` | Claude quota scraper and watcher; polls `/usage` via hidden tmux window; publishes NATS threshold events and `hw.events.usage.claude.snapshot`; stores snapshots in SQLite and NATS KV; statusline reads KV first, falls back to SQLite; threshold alerts delivered via `Notifier` |
| `quota_db.py` | SQLite persistence for quota tracking (`~/.local/state/ai-cli/quota.db`); stores usage records, snapshots, weekly reset anchors, and `notification_log` (full delivery history with per-channel success/failure) |
| `cc_usage.py` | CC JSONL scanner for per-call token data; cursor-tracked incremental push to ai-core REST API; defines `CCTokenEvent`, `PushResult` |
| `messaging.py` | Async NATS client wrapper with JetStream; SSH tunnel auto-open on Mac when port 4222 is unreachable; defines `NATSClient`, stream configs, heartbeat/event helpers |
| `sync.py` | Bidirectional CC session data sync (conversation JSONL + memory files) via bare git staging repo over SSH; defines `SyncConfig` |
| `telemetry.py` | Event recording pipeline: caller → NATS JetStream → background writer → SQLite (`~/.ai-cli/telemetry.db`) |
| `research.py` | Multi-step research depth orchestration (Planner-Executor); per-step checkpointing under `~/.local/state/ai-cli/research-runs/` |
| `spend.py` | Gemini cost reporting; aggregates local JSONL logs + optional GCP BigQuery billing export |
| `memory.py` | inotify/FSEvents-based memory file watcher; debounces MEMORY.md writes; publishes `memory.dream.*` NATS events |
| `notifications.py` | Unified notification delivery: `Notifier` class fires all configured channels in parallel (Discord webhook, ntfy push, OS native); `NotificationResult` per-channel result tracking; OS fallback fires when all primaries fail; `NotificationManager` for iTerm2 badge updates via OSC escape sequences |
| `vpn_watch.py` | Circus-managed daemon; polls VPN state; publishes `vpn.state.changed` to NATS |
| `process_hygiene.py` | Orphaned process detection and cleanup for mosh-server, signal-watch, autossh, circusd, nats-server |
| `layout.py` | iTerm2 YAML layout templating; generates Dynamic Profiles; builds windows/panes via iTerm2 Python API |
| `icon_generator.py` | Runtime iTerm2 icon tinting via Pillow; computes complementary HSL tint from tab color |
| `copier_update.py` | Runs `copier update` across all `project-template`-based projects; scans for conflict markers |
| `setup.py` | Detects managed vs. standalone environment; switches `CLAUDE.md` variant accordingly |
| `data/statusline-command.sh` | Shell script deployed by `ai update`; called by the iTerm2 statusline |

---

## Command Groups

### Session Management
- `ai c [N] [-p PROJECT] [-R]` — launch or resume Claude Code tmux session with git worktree isolation
- `ai g [N] [-p PROJECT]` — launch or resume Gemini CLI tmux session
- `ai ls [-a]` — fzf-powered session picker sorted by activity
- `ai attach SESSION` — attach to named tmux session
- `ai reconnect [-p PROJECT] [-f]` — reconnect to most recent matching session

### Gemini / Research
- `ai gemini PROMPT [-m MODEL] [-o FILE] [-d DEPTH] [-s TIER] [--resume RUN_ID]` — run Gemini with 3-tier auth fallback and optional research depth orchestration
- `ai spend gemini` — Gemini cost report (JSONL logs + optional BigQuery)

### Quota Tracking
- `ai quota watch start|stop|status` — manage quota-watch Circus daemon lifecycle
- `ai quota watch run [-i/--interval]` — raw daemon entry point (Circus uses this)
- `ai quota scrape` — one-shot quota scrape, store, and publish
- `ai quota status` — read current quota from SQLite (with KV sync if stale)

### Notifications
- `ai notifications list` — audit configured channels (name, enabled, credentials present/absent)
- `ai notifications log [-n N] [-s SINCE] [-f FROM] [-t TO] [--source TEXT] [--failed]` — query notification delivery history

### CC Token Tracking
- `ai cc-usage push` — scan CC JSONL files, push new token events to ai-core REST API
- `ai cc-usage status` — show cursor state and last-push summary

### Sync
- `ai sync push/pull/conflicts/watch [-m/-f]` — sync CC session data between machines via bare git repo

### Handoff Queue
- `ai handoff post/check/claim/complete` — delegate tasks between sessions and machines

### Daemons / Process Management
- `ai ps [clean]` — inspect and clean up stale ai-cli processes and PID files
- `ai signal-watch start|stop|status` — Circus-managed NATS handoff subscriber per CC session
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
- `ai copier-update [--project NAME]` — propagate project-template changes to all downstream projects
- `ai internal publish SUBJECT PAYLOAD` — raw NATS publish (used by hooks)

---

## Key Data Types

| Type | Module | Key Fields |
|------|--------|------------|
| `GeminiResult` | `gemini.py` | `content`, `model`, `tier` (1/2/3), `success`, `error`, `duration_ms`, `input_tokens`, `output_tokens`, `total_tokens`, `event_id`, `machine` |
| `AttemptLog` | `gemini.py` | Per-attempt log within a `GeminiResult` |
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
| `hw.events.usage.gemini.event` | `gemini.py` | Per-Gemini-call event (ai-core ingest) |
| `hw.events.usage.claude.snapshot` | `quota.py` | Claude quota snapshot (ai-core ingest) |
| `handoff.{project}` | `handoff.py` | Cross-session task delegation |
| `memory.dream.started/completed` | `memory.py` | Memory consolidation lifecycle |

**NATS KV bucket `hw_state`:**
- `quota.claude.current` — latest `QuotaSnapshot` JSON; written by `quota.py`, read by statusline and `ai quota status`

### ai-core REST API (optional)
- `POST /api/v1/usage/cc/ingest` — CC token events from `cc_usage.py`
- `GET /api/v1/usage/claude/current` — read Claude quota tier (used by aido)
- `GET /api/v1/usage/gemini/balance` — read Gemini quota tier (used by aido)

### SQLite Databases
- `~/.local/state/ai-cli/quota.db` — quota snapshots, usage records, weekly state
- `~/.ai-cli/telemetry.db` — telemetry events with subject/machine/session index

### External Processes / Services
- **tmux** (via `libtmux`) — session creation, attachment, hidden quota-scrape windows
- **Circus** — process supervisor for `signal-watch`, `vpn-watch`, `telemetry writer` daemons
- **SSH/mosh** — remote session launch; SSH tunnels for NATS access on Mac
- **Gemini CLI** (`gemini` binary) — tier-1 OAuth auth in the fallback chain
- **Google AI Studio REST API** — tiers 2 (free key) and 3 (paid key) via `google-genai` SDK
- **GCP BigQuery** (optional) — billing data for `ai spend gemini`
- **watchdog** — inotify/FSEvents for memory file watching and sync watch
- **fzf** — interactive session picker in `ai ls`
- **copier** — project template propagation in `ai copier-update`

### Files on Disk
- `~/.claude/projects/` — CC session JSONL files (scanned by `cc_usage.py`, synced by `sync.py`)
- `~/.local/state/ai-cli/gemini-logs/YYYY-MM-DD.jsonl` — per-Gemini-call run logs
- `~/.local/state/ai-cli-utils/cc-usage-cursor.json` — per-session JSONL scan cursors
- `~/.local/state/ai-cli/research-runs/<run-id>/` — research depth step checkpoints

---

## Key Design Decisions

**3-tier Gemini auth fallback** — `gemini.py` tries OAuth (Gemini CLI binary) → free API key (`GOOGLE_API_KEY_FREE_TIER`, Flash/Gemma/Embedding models only) → paid API key (`GOOGLE_API_KEY_TIER_1`). Paid tier disabled by default (`paid_fallback_enabled = false`). Automatic retry on capacity errors at each tier before falling to the next.

**Dual-path quota state** — `quota.py` writes each snapshot to both NATS KV (`quota.claude.current`) and local SQLite. The statusline reads KV first with a 300ms thread timeout, falls back to SQLite. This keeps Mac and Hetzner statuslines aligned without requiring a local scrape on every machine.

**Git worktree isolation** — each `ai c N` session runs in `.worktrees/sw-N/` on branch `wt-sw-N`, **created at `origin/main`** and tracking `origin/main`. Created and destroyed by `main.py`. Commits ship directly to `origin/main` via `git push origin HEAD:main`. Base and upstream are both pinned deliberately: `git worktree add -b` defaults its start-point to the main tree's current HEAD, which silently produces a branch that is not PR-clean whenever that tree is parked on something other than `main` (BUG-004). An unresolvable `origin/main` is a hard failure, never a fallback to HEAD.

**CC session sync via bare git** — `sync.py` uses a bare git repo as the transport for CC JSONL + memory files between machines. Conflict detection is file-level mtime comparison logged to `~/.claude-sync-conflicts.log`.

**Cursor-based CC token tracking** — `cc_usage.py` tracks last-seen `occurred_at` per session UUID in `cc-usage-cursor.json`. Only entries newer than the cursor are pushed. Idempotent: ai-core ingest deduplicates by `event_id`.

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
| `google-genai>=1.0.0` | Google AI Studio REST API (tiers 2 and 3) |
| `pyyaml>=6.0` | Layout YAML parsing |
| `pillow>=10.0` | iTerm2 icon tinting |
| `google-cloud-bigquery` | (optional) GCP billing for `ai spend gemini` |
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
