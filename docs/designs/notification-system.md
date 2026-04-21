---
title: "Quota Notification System"
category: design
tags: [quota, notifications, circus, ntfy, discord, process-management]
status: DRAFT
source: session-2026-04-20
task: AI-CLI-25
---

# Quota Notification System

**Status:** DRAFT

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
- [Notification Channel System](#notification-channel-system)
- [Configuration Schema](#configuration-schema)
- [quota-watch Daemon and Circus Auto-Start](#quota-watch-daemon-and-circus-auto-start)
- [Implementation Phases](#implementation-phases)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Problem Statement

`ai quota watch` polls Claude usage and fires threshold alerts (50%, 75%, 90%), but it has two gaps:

1. **No auto-start** — the watcher must be launched manually. If it crashes or the session restarts, alerts stop silently.
2. **Notification delivery** — the system needs reliable multi-channel delivery so alerts reach the user regardless of what device they're on and whether a desktop session is active.

Both gaps have the same root: quota-watch is not managed as a system service.

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Process management for quota-watch | PID file + manual restart, systemd, Circus | Circus | Already a declared dependency; consistent with signal-watch | Approved |
| 2 | Notification channel hierarchy | Single channel, parallel all-channels, priority fallback | Parallel primary + OS fallback | Discord and ntfy fire independently; OS notification only when neither is configured | Approved |
| 3 | Config source | Config file only, env vars only, layered | Layered (config file → env var) | Allows Doppler injection without requiring file edits; consistent with existing ai-cli config pattern | Approved |

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

**Parallel delivery for primary channels, OS fallback only when no primary is configured.** Discord webhook and ntfy fire independently — both succeed or fail independently. The OS fallback (osascript/notify-send) is last resort for users with no webhook configured, not a backup for webhook failures.

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

> **Feedback Round 2:** Your approval/feedback on updated D3 and overall design:
> - <enter feedback here>

## Notification Channel System

### Channel Hierarchy

```text
quota threshold crossed
        │
        ├─── Discord webhook (if DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL configured)
        │         POST {"content": "..."} — auto-detected from url containing "discord.com"
        │
        ├─── ntfy (if NTFY_BASE_URL + NTFY_TOPIC configured)
        │         POST to {base}/{topic} with Title/Priority/Tags headers
        │         Bearer token auth if NTFY_TOKEN set
        │
        └─── OS fallback (only if neither Discord nor ntfy is configured)
                  macOS: osascript display notification
                  Linux: notify-send
```

Both Discord and ntfy fire in parallel — a failure in one does not affect the other. Errors are logged to stderr but never raise.

### Message Format

**Discord:** Slack-compatible markdown, auto-detected by URL:
```text
:rotating_light: *Claude quota 90% threshold crossed*
Weekly (all models): 90.2% | Sonnet: 45.1% | Session: 12.3%
*Slow down — quota nearly exhausted.*
```

**ntfy:** Plain text body with structured headers:
```text
Title: Claude quota 90% threshold crossed
Priority: urgent (90%) / high (75%) / default (50%)
Tags: rotating_light (90%) / warning (75%+)
Body: Weekly (all models): 90.2% | Sonnet: 45.1%
```

### Priority Mapping

| Threshold | ntfy Priority | ntfy Tag | Discord Emoji |
|-----------|--------------|----------|---------------|
| 90% | `urgent` | `rotating_light` | `:rotating_light:` |
| 75% | `high` | `warning` | `:warning:` |
| 50% | `default` | `warning` | `:information_source:` |

> **Feedback Round 1:** Does this approach feel right? What's missing?
> - <enter feedback here>

## Configuration Schema

### config.toml `[quota]` section

```toml
[quota]
# Incoming webhook URL — Discord or Slack auto-detected by URL
webhook_url = ""

# ntfy server config
ntfy_base_url = ""   # e.g. "https://ntfy.example.com"
ntfy_topic = ""      # e.g. "my-alerts"
ntfy_token = ""      # Bearer token (optional for public topics)

# quota-watch poll interval in seconds (default: 300)
poll_interval = 300
```

### Environment Variables (override config.toml)

| Variable | Purpose |
|----------|---------|
| `DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL` | Discord incoming webhook URL |
| `NTFY_BASE_URL` | ntfy server base URL |
| `NTFY_TOPIC` | ntfy topic name |
| `NTFY_TOKEN` | ntfy Bearer token |

All four are managed in Doppler (`project: ai-cli-utils`, `config: dev`) and injected at runtime via direnv/Doppler.

## quota-watch Daemon and Circus Auto-Start

### Current State

`ai quota watch` runs as a foreground process with a PID file guard (`quota-watch.pid`). It must be started manually and does not restart on crash.

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

Threshold alerts are deduplicated per calendar day using an in-memory dict (`alerted_today: dict[int, str]`). If quota-watch restarts mid-day (crash + respawn), it loses in-memory state and may re-alert the same threshold. This is acceptable: better to get a duplicate alert than to miss one. A persistent dedup store (SQLite `quota_alerts` table) can be added later if duplicate noise becomes a problem.

> **Feedback Round 1:** Does the Circus wiring feel right — any concerns about the `run` subcommand or session script integration?
> - <enter feedback here>

## Implementation Phases

### Phase 1 — Circus wiring (next)

1. Add `ai quota watch run` subcommand as raw daemon entry point (no PID file guard)
2. Update `ai quota watch` to dispatch `start/stop/status/run`
3. Extend `_ensure_circusd()` to register quota-watch watcher in `circus.ini`
4. Add `ai quota watch start` to session launch template
5. Tests: start/stop/status, Circus config generation, session template integration

### Phase 2 — Persistent dedup (later, if needed)

Add `quota_alerts` table to `quota.db` to survive restarts without re-alerting. Gate on whether duplicate alert noise is actually a problem in practice.

> **Feedback Round 1:** Does the phasing feel right — too big, too small?
> - <enter feedback here>

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | circusd not running when session starts | quota-watch never starts | `_ensure_circusd()` starts circusd if not running — same pattern as signal-watch |
| 2 | Duplicate alerts after crash+respawn | Alert noise | Acceptable — dedup can be persisted in Phase 2 if needed |
| 3 | ntfy token rotation leaves old token in config | Silent ntfy failures | Token is in Doppler, not config.toml — rotation updates Doppler only |
| 4 | Both webhook and ntfy down simultaneously | No alert delivery | OS fallback fires if neither fires (though today OS fallback only fires when unconfigured — see Open Questions) |

## Open Questions

1. Should the OS fallback fire as a true last resort (when configured channels fail) rather than only when no channel is configured? Currently it only fires when neither Discord nor ntfy is configured.
2. Should quota-watch deduplication persist across restarts in Phase 1, or defer to Phase 2?
3. Should `ai quota watch status` show the last N alerts fired, or just process state?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. OS fallback should fire both when no channel is configured AND as a true last resort when configured channels fail. But a channel should always be configured — why would no channel ever be configured in the first place? Also: add a unified notification API to the design — single interface that fires all configured channels (ntfy, Discord, OS), customizable via config and per-call overrides. Should be easy to configure notifications for any project. What's the best approach for managing and auditing all configured notifications — database or config file?
> 2. Do all phases in one implementation — no unnecessary phasing/refactoring. Restructure the doc accordingly.
> 3. Show last N alerts (N as CLI arg), or since DATE/DATETIME, or date range (--from/--to). Design this in the doc.

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-20 | Round 1 | D1 (Circus) approved. D2 (parallel channels) approved — keep both Discord and ntfy; AI-CLI-49 added to evaluate long-term. D3 (layered config) approved after expanding with options/pros/cons/recommendation. |
