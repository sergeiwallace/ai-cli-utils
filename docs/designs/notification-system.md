---
title: "Quota Notification System"
category: design
tags: [quota, notifications, circus, ntfy, discord, process-management]
status: approved
source: session-2026-04-20
task: AI-CLI-25
---

# Quota Notification System

**Status:** Approved

**Created:** 2026-04-20

**Task:** AI-CLI-25

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

## Table of Contents

- [Problem Statement](#problem-statement)
- [Design Decisions](#design-decisions)
- [Notifier API](#notifier-api)
- [Configuration Schema](#configuration-schema)
- [Notification Log](#notification-log)
- [quota-watch Daemon and Circus Auto-Start](#quota-watch-daemon-and-circus-auto-start)
- [Implementation Plan](#implementation-plan)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Problem Statement

`ai quota watch` polls Claude usage and fires threshold alerts (50%, 75%, 90%), but it has three gaps:

1. **No auto-start** — the watcher must be launched manually. If it crashes or the session restarts, alerts stop silently.
2. **No unified notification API** — notification logic is embedded in `quota.py` with no reusable interface for other callers or projects.
3. **No notification history** — there is no record of when alerts fired, which channels succeeded, or what was sent.

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Process management for quota-watch | PID file + manual restart, systemd, Circus | Circus | Already a declared dependency; consistent with signal-watch | Approved |
| 2 | OS fallback behaviour | Only when no primary channel configured, always as last resort | Always fire on failure too | Better than silent miss; a channel should always be configured but new installs need protection | Approved |
| 3 | Config source | Config file only, env vars only, layered | Layered (config file → env var) | Allows Doppler injection without requiring file edits; consistent with existing ai-cli config pattern | Approved |
| 4 | Notifier API storage | Database, config file, hybrid | Config file for channels + SQLite for history | Config is static structure (config file); history is mutable state (SQLite) | Approved |

### Decision Details

#### Decision 1: Process Management for quota-watch

##### (a) PID file + manual restart

**Pros:**
- Simple — no new runtime dependency

**Cons:**
- No restart-on-crash
- User must remember to start it
- Inconsistent with signal-watch (already Circus-managed)

##### (b) systemd

**Pros:**
- Standard Linux service management
- Full journal integration

**Cons:**
- macOS incompatible (launchd instead)
- Adds platform-specific wiring
- Not how signal-watch works

##### (c) Circus

**Pros:**
- Already a declared dependency (`circus>=0.18.0`)
- Shared `circusd` with signal-watch — one daemon manages both
- `respawn=True` gives automatic restart on crash
- Platform-agnostic (Python, works on macOS and Linux)
- `circusctl` control interface already wired for signal-watch

**Cons:**
- Circus is a heavier runtime than a plain PID file

##### Recommendation

**Circus.** It's already installed and already managing signal-watch. Adding quota-watch as a second watcher in the same `circus.ini` is a trivial extension of existing infrastructure. The shared daemon model means one `circusd` process manages all long-running ai-cli daemons.

---

#### Decision 2: Notification Channel Hierarchy

##### (a) Single primary channel

**Pros:**
- Simple config

**Cons:**
- Single point of failure — if Discord is down or the webhook is wrong, alerts are silent

##### (b) Parallel all-channels (fire every configured channel simultaneously)

**Pros:**
- Redundant delivery
- No priority logic needed

**Cons:**
- Duplicate notifications if multiple channels are configured (acceptable — better than missing one)

##### (c) Priority fallback (try A, then B if A fails)

**Pros:**
- No duplicates

**Cons:**
- Sequential — a slow primary delays the backup
- If primary silently accepts (HTTP 200) but doesn't deliver, backup never fires

##### Recommendation

**Parallel delivery for all primary channels; OS fallback always fires when all primaries fail (or when none are configured).** A channel should always be configured, but new installs need protection before setup is complete. The OS fallback ensures no alert is silently dropped.

---

#### Decision 3: Config Source

##### (a) Config file only

**Pros:**
- Explicit — all config in one place
- Easy to audit what's configured

**Cons:**
- Secrets in a plain file on disk — rotation requires file edits
- No Doppler / secrets manager injection without extra tooling

##### (b) Environment variables only

**Pros:**
- Works naturally with Doppler, direnv, and 12-factor tooling
- No secrets on disk

**Cons:**
- No persistent defaults — user must set vars every session or wire them into shell init
- Harder to distribute across machines without a secrets manager

##### (c) Layered (config file → env var fallback)

**Pros:**
- Secrets stay in Doppler (env vars), never in files
- Config file can still hold non-secret defaults (poll interval, topic name)
- Consistent with how other ai-cli config options work

**Cons:**
- Two sources of truth require documenting precedence clearly

##### Recommendation

**Option (c): layered.** Config file holds non-secret defaults; env vars (injected by Doppler/direnv) hold credentials. This matches the existing pattern in `_send_notification` and lets users configure the system without touching files.

---

#### Decision 4: Notifier API — Channel Config Storage and History

##### (a) Database for both channel config and history

**Pros:**
- Single source of truth

**Cons:**
- Channel config is static structure — a database is overkill
- Requires schema migration to change channel config
- Harder to read/edit by hand

##### (b) Config file for both channel config and history

**Pros:**
- Everything in one place

**Cons:**
- History is mutable append-only state — a flat file is the wrong shape
- No efficient querying by time range or source

##### (c) Config file for channel config + SQLite for history

**Pros:**
- Config file is the right shape for static channel definitions — readable, auditable, version-controllable
- SQLite is the right shape for notification history — queryable, indexed, persistent across restarts
- Consistent with how quota snapshots are stored (already SQLite in `quota.db`)

**Cons:**
- Two storage mechanisms, but they serve fundamentally different purposes

##### Recommendation

**Option (c): config file for channel config, SQLite for notification history.** Channel config is something you set up once and audit occasionally — a TOML file is perfect. Notification history is mutable append-only state that needs to be queried by time and source — SQLite is the right fit and is already in use for quota data.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. Decision 1 (Circus): Recommendation approved.
> 2. Decision 2 (Parallel channels): Keep both Discord and ntfy until evaluated in practice. Added AI-CLI-49 to roadmap to evaluate notification channels after using them for a while.
> 3. Decision 3 (Layered config): Missing options/pros/cons/recommendation — expand.
>
> **AI Response Round 1:**
> - D1: Circus approved, no changes.
> - D2: Parallel delivery confirmed. Roadmap task AI-CLI-49 added.
> - D3: Expanded with three options (config file only, env vars only, layered), pros/cons for each, and recommendation for layered.

---

> **Feedback Round 2:** Your approval/feedback on D4 and revisions:
> - <enter feedback here>

## Notifier API

### Overview

`ai_cli.notifications` exposes a `Notifier` class — the single entry point for all notification delivery. Internal callers (quota-watch, future daemons) and external code (other projects) all use the same interface.

```python
from ai_cli.notifications import Notifier

n = Notifier()
n.send("Build complete", "project-x passed all tests")

# Target specific channels only
n.send("Critical alert", "quota at 90%", priority="urgent", channels=["discord", "ntfy"])

# Override config at construction time
n = Notifier(channels_config={"ntfy": {"base_url": "...", "topic": "..."}})
```

### `Notifier` Class

```python
class Notifier:
    def __init__(self, channels_config: dict | None = None) -> None:
        """Load channel config from config.toml + env vars, or accept an override dict."""

    def send(
        self,
        title: str,
        body: str,
        priority: str = "default",      # "urgent" | "high" | "default" | "low"
        tags: list[str] | None = None,
        channels: list[str] | None = None,  # None = all enabled channels
        source: str = "",               # caller identifier, stored in notification_log
    ) -> list[NotificationResult]:
        """Fire all enabled channels in parallel. Returns per-channel results."""
```

### `NotificationResult`

```python
@dataclass
class NotificationResult:
    channel: str       # "discord" | "ntfy" | "os"
    success: bool
    status_code: int | None
    error: str | None
```

### Delivery Logic

1. Collect enabled channels from config (Discord, ntfy, OS — whichever are configured and enabled).
2. Fire all primary channels concurrently (thread pool, short timeout per channel).
3. If **all primary channels fail or none are configured**, fire the OS fallback.
4. Log each result to `notification_log` in SQLite.
5. Return results to caller — never raise.

> **Feedback Round 1:** Does this approach feel right? What's missing?
> - <enter feedback here>

## Configuration Schema

### `[notifications]` section in config.toml

Non-secret defaults live here. Secrets (URLs, tokens) are in env vars.

```toml
[notifications]
# OS fallback fires when all primary channels fail or when none are configured
os_fallback = true

[notifications.discord]
# enabled defaults to true when DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL is set
enabled = true

[notifications.ntfy]
# enabled defaults to true when NTFY_BASE_URL + NTFY_TOPIC are set
enabled = true
# Non-secret defaults (topic, priority cap) may be set here
# topic = "my-alerts"  # overridden by NTFY_TOPIC env var
```

### Environment Variables

| Variable | Channel | Purpose |
|----------|---------|---------|
| `DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL` | discord | Incoming webhook URL |
| `NTFY_BASE_URL` | ntfy | Server base URL, e.g. `https://ntfy.example.com` |
| `NTFY_TOPIC` | ntfy | Topic name |
| `NTFY_TOKEN` | ntfy | Bearer token (omit for public topics) |

All four managed in Doppler and injected via direnv/Doppler.

### Auditing Configured Channels

```text
ai notifications list
```

Prints each channel, its enabled state, and whether credentials are present (not values):

```text
Channel   Enabled   Credentials
discord   yes       webhook_url: set
ntfy      yes       base_url: set, topic: set, token: set
os        yes       (no credentials needed)
```

## Notification Log

### SQLite Schema

New table added to the existing `quota.db`:

```sql
CREATE TABLE notification_log (
    id          INTEGER PRIMARY KEY,
    fired_at    TEXT NOT NULL,          -- ISO UTC timestamp
    source      TEXT NOT NULL,          -- e.g. "quota-watch", "manual"
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'default',
    tags        TEXT,                   -- JSON array, nullable
    channels_attempted  TEXT NOT NULL,  -- JSON array: ["discord", "ntfy"]
    channels_succeeded  TEXT NOT NULL,  -- JSON array: ["discord"]
    channels_failed     TEXT NOT NULL   -- JSON array: ["ntfy"]
);
CREATE INDEX notification_log_fired_at ON notification_log (fired_at);
CREATE INDEX notification_log_source   ON notification_log (source);
```

### `ai notifications log` CLI

```text
ai notifications log [OPTIONS]

Options:
  -n, --last INTEGER          Show last N notifications (default: 10)
  -s, --since DATETIME        All notifications since this datetime (ISO or
                              natural: "2h", "yesterday", "2026-04-01")
  -f, --from DATE             Start of date range (inclusive)
  -t, --to DATE               End of date range (inclusive)
      --source TEXT           Filter by source (e.g. "quota-watch")
      --failed                Show only notifications where at least one
                              channel failed
```

Examples:

```text
ai notifications log                     # last 10
ai notifications log -n 50               # last 50
ai notifications log --since 2h          # last 2 hours
ai notifications log -f 2026-04-01 -t 2026-04-07
ai notifications log --source quota-watch --failed
```

Output format (table):

```text
Time                  Source        Title                           Channels
2026-04-20 14:32:01   quota-watch   Claude quota 75% threshold...  discord✓ ntfy✓
2026-04-20 09:11:44   quota-watch   Claude quota 50% threshold...  discord✓ ntfy✗
```

## quota-watch Daemon and Circus Auto-Start

### Target State

`ai quota watch start` registers quota-watch in `circus.ini` under the shared `circusd` instance (same one used by signal-watch). `respawn=True` means circusd automatically restarts it if it exits. The session launch script starts quota-watch alongside signal-watch.

### Circus Watcher Config (added to circus.ini)

```ini
[watcher:quota-watch]
cmd = ai quota watch run
numprocesses = 1
respawn = True
singleton = True
stdout_stream.class = FileStream
stdout_stream.filename = {state_dir}/quota-watch.log
stderr_stream.class = FileStream
stderr_stream.filename = {state_dir}/quota-watch.log
```

The `run` subcommand is the raw daemon entry point (no PID file guard, no Circus wrapping — Circus owns process lifecycle). The existing `ai quota watch` command becomes a `start/stop/status` dispatcher.

### CLI Interface

```text
ai quota watch start    — add watcher to circus.ini, ensure circusd running
ai quota watch stop     — remove watcher from circus.ini (or circusctl stop)
ai quota watch status   — circusctl stats quota-watch + last log line
ai quota watch run      — raw daemon entry point (used by Circus, not by users directly)
```

### Session Script Integration

In the session launch bash template (same location as `ai signal-watch start`):

```bash
ai quota watch start 2>/dev/null || true
```

This is idempotent — if quota-watch is already registered and running, it no-ops.

### Deduplication

Threshold alerts are deduplicated per calendar day using an in-memory dict (`alerted_today: dict[int, str]`). If quota-watch restarts mid-day (crash + respawn), it loses in-memory state and may re-alert the same threshold. This is acceptable: better to get a duplicate alert than to miss one.

The `notification_log` table provides a persistent record so duplicates are visible after the fact.

> **Feedback Round 1:** Does the Circus wiring feel right — any concerns about the `run` subcommand or session script integration?
> - <enter feedback here>

## Implementation Plan

1. Create `src/ai_cli/notifications.py` — `Notifier`, `NotificationResult`, channel drivers (Discord, ntfy, OS)
2. Move `_send_webhook_notification`, `_send_ntfy_notification`, `_send_notification` from `quota.py` into `notifications.py` as channel drivers behind `Notifier`
3. Add `notification_log` table migration to `quota_db.py` (alongside existing quota tables)
4. Add `ai notifications log` command with `--last`, `--since`, `--from`, `--to`, `--source`, `--failed` options
5. Add `ai notifications list` command (audit configured channels)
6. Update `quota_watch()` to use `Notifier` instead of calling send functions directly
7. Add `ai quota watch run` subcommand (raw daemon entry point for Circus)
8. Update `ai quota watch` dispatcher to handle `start/stop/status/run`
9. Extend `_ensure_circusd()` to register quota-watch watcher in `circus.ini`
10. Add `ai quota watch start` to session launch template
11. Tests: Notifier delivery logic, channel drivers, notification_log writes, log CLI, Circus wiring

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | circusd not running when session starts | quota-watch never starts | `_ensure_circusd()` starts circusd if not running — same pattern as signal-watch |
| 2 | Duplicate alerts after crash+respawn | Alert noise | In-memory dedup is best-effort; `notification_log` makes duplicates visible |
| 3 | ntfy token rotation leaves old token in config | Silent ntfy failures | Token is in Doppler, not config.toml — rotation updates Doppler only |
| 4 | All primary channels fail simultaneously | No alert delivery | OS fallback always fires when all primaries fail |
| 5 | OS fallback unavailable (headless server) | Silent miss on OS fallback | OS fallback failure is logged; primary channels should be configured on servers |

## Open Questions

1. Should the OS fallback fire as a true last resort (when configured channels fail) rather than only when no channel is configured? Currently it only fires when neither Discord nor ntfy is configured.
2. Should quota-watch deduplication persist across restarts in Phase 1, or defer to Phase 2?
3. Should `ai quota watch status` show the last N alerts fired, or just process state?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. OS fallback should fire both when no channel is configured AND as a true last resort when configured channels fail. But a channel should always be configured — why would no channel ever be configured in the first place? Also: add a unified notification API to the design — single interface that fires all configured channels (ntfy, Discord, OS), customizable via config and per-call overrides. Should be easy to configure notifications for any project. What's the best approach for managing and auditing all configured notifications — database or config file?
> 2. Do all phases in one implementation — no unnecessary phasing/refactoring. Restructure the doc accordingly.
> 3. Show last N alerts (N as CLI arg), or since DATE/DATETIME, or date range (--from/--to). Design this in the doc.
>
> **AI Response Round 1:**
> - OQ1: Agreed — OS fallback now fires when all primaries fail, not only when unconfigured. D2 updated accordingly. A channel should always be configured; the OS fallback protects new installs before setup is complete. Unified `Notifier` API added as a new section. D4 added: config file for channel config (static structure, auditable), SQLite for notification history (queryable state). `ai notifications list` audits configured channels. `ai notifications log` queries history.
> - OQ2: Phasing removed — single flat implementation plan, no refactoring steps.
> - OQ3: `ai notifications log` designed with `--last N`, `--since DATETIME`, `--from`/`--to` date range, `--source`, `--failed`. Default shows last 10. See Notification Log section.

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-20 | Round 1 | D1 (Circus) approved. D2 (parallel channels) approved — keep both Discord and ntfy; AI-CLI-49 added to evaluate long-term. D3 (layered config) approved after expanding with options/pros/cons/recommendation. |
| 2026-04-20 | Round 2 | OQ1 → OS fallback fires on failure too; Notifier API added; D4 added (config file + SQLite hybrid). OQ2 → phases removed, flat implementation plan. OQ3 → ai notifications log CLI designed. |
| 2026-04-20 | Round 3 | D4 (Notifier API storage: config file + SQLite) approved. All decisions approved — doc ready for implementation. |
