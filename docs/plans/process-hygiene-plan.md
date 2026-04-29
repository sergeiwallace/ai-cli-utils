# Process Hygiene (`ai ps`) — Implementation Plan

**Status:** DRAFT
**Created:** 2026-04-04
**Task:** `[AI-CLI-28]`

## Table of Contents

- [Overview](#overview)
- [Process Catalog](#process-catalog)
- [Orphan Detection and Scoring](#orphan-detection-and-scoring)
- [Options](#options)
- [CLI Design](#cli-design)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

ai-cli manages several long-running background processes (mosh-server, signal-watch, autossh tunnels, circus workers) across two machines. When sessions end abnormally — network drops, mosh client disconnects, CC crashes — server-side processes are left running indefinitely with no owner. Today this requires manual inspection and killing. Five orphaned `mosh-server` processes from March 2026 were found on Mac and killed manually, triggering this feature.

Goal: `ai ps` shows a live inventory of all ai-managed processes with age and status. `ai ps clean` kills orphans, with behavior gated on a confidence score — high-confidence orphans can be auto-killed, low-confidence ones are flagged for manual review. Hetzner results are served from a local cache refreshed in the background to avoid SSH latency on every invocation.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - Scope: ai-managed only for now. Include anything related to ai-cli-utils usage and Termius iOS connections (those are mosh-server processes already in scope).
> - Auto-hygiene: find a middle ground — explicit-only won't get used enough. Use the scoring system to gate auto-kill to high-confidence orphans only, flag low-confidence ones.
> - Remote: cache Hetzner process state locally and poll periodically to avoid latency.
> - New module: yes.
>
> **AI Response Round 1:**
> - Scope confirmed: ai-managed processes (mosh-server, signal-watch, autossh, circus, memory/sync watch). Termius connections create mosh-server processes already tracked.
> - Auto-hygiene: score-gated approach — score ≥ 80 → auto-kill on session start + daily cron; score 40–79 → flag in `ai ps` output, require `ai ps clean` to kill; score < 40 → active, no action.
> - Remote caching: Hetzner process state cached at `~/.local/state/ai-cli/remote-ps-cache.json` with 30-minute TTL. Refreshed async in background. `ai ps` reads cache instantly; `ai ps --refresh` forces SSH re-check.
> - New module: `src/ai_cli/process_hygiene.py`.

---

## Process Catalog

Processes tracked by `ai ps`:

| Process | Where | Orphan risk | Notes |
|---------|-------|-------------|-------|
| `mosh-server` | Mac + Hetzner | **High** — client disconnect leaves server running | Main motivator; Termius iOS sessions also create these |
| `signal-watch` | Mac + Hetzner | Medium — tied to tmux session that may be gone | Has PID file at `~/.local/state/ai-cli/signal-watch-<project>.pid` |
| `autossh` tunnels | Mac | Low — managed by `ai tunnel`, rarely stale | 2 persistent tunnels currently |
| `circusd` | Mac | Low — intentionally persistent daemon | Report status only; don't auto-kill |
| `nats-server` | Hetzner | Low — intentionally persistent | Report status only; don't kill |
| `ai memory watch` / `ai sync watch` | Mac | Medium — launched per tmux session | Orphaned if parent tmux session is gone |

**Not tracked:** pytest/uv test processes (transient), claude CLI itself (the main process).

## Orphan Detection and Scoring

Each process gets a **staleness score** (0–100). Score determines action:

| Score | Verdict | Auto-behavior |
|-------|---------|---------------|
| ≥ 80 | **orphaned** | Auto-kill on session start + daily cron |
| 40–79 | **suspect** | Flagged in `ai ps` output; requires `ai ps clean` |
| < 40 | **active** | No action |

### mosh-server scoring

| Signal | Points |
|--------|--------|
| No `mosh-client` with matching UDP port (`lsof -i UDP`) | +50 |
| Age > 24h | +20 |
| Age > 6h | +10 |
| Matching tmux session unattached for > 2h | +5 |
| No matching tmux session at all | +10 |
| Matching tmux session **currently attached** | -10 |
| `lsof` unavailable and age > 48h | +60 (fallback) |

**Note:** killing a mosh-server is safe regardless — it only drops the mosh tunnel, not the tmux session. The tmux session persists and all work is intact. The user can always reconnect (mosh starts a fresh server). The -10 for an actively-attached session is purely a conservative guard, not a safety necessity.

Example: orphaned mosh-server from March with no client, no tmux = 50+20+10+10 = **90 → auto-kill**.
Example: Termius orphan, no client, tmux unattached 8h, age 26h = 50+20+10+5 = **85 → auto-kill**.
Example: mosh-server 1h old, client connected, tmux attached = 0-10 = **0 → active**.

### signal-watch scoring

| Signal | Points |
|--------|--------|
| PID in pidfile no longer running | +70 |
| Tmux session it watches no longer exists | +30 |
| Age > 12h with no pidfile update | +20 |

### memory watch / sync watch scoring

| Signal | Points |
|--------|--------|
| Parent tmux session no longer exists | +60 |
| Age > 24h | +20 |

### autossh / circusd / nats-server
Score always < 40 (these are intentionally persistent). `ai ps` reports them as informational only; `ai ps clean` never touches them.

### Hetzner remote cache

- Cache stored at: `~/.local/state/ai-cli/remote-ps-cache.json`
- TTL: 30 minutes (configurable in `[process_hygiene]` config section)
- Refresh: async background SSH call on first `ai ps` run after TTL expires
- `ai ps --refresh` forces immediate SSH re-check
- Cache includes: pid, name, age_seconds, score, args for each remote process

## Options

### Option A: `ai ps` as standalone subcommand + `process_hygiene.py` module ✓ **Selected**

New `process_hygiene.py` module, wired into `main.py` as `ai ps [clean] [--remote] [--refresh] [--force]`.

**Pros:**
- Discoverable (`ai --help` lists it)
- Consistent with existing `ai tunnel`, `ai signal-watch` pattern
- Clean module boundary; testable in isolation
- Easy to call from session-start hook and cron

**Cons:**
- New module to maintain

### Option B: Integrate into existing commands

Spread across `ai signal-watch status`, `ai tunnel status`, etc.

**Pros:** No new top-level command

**Cons:** Cross-domain processes (mosh + circus + watchers) have no single home; `ai ps clean` has nowhere to live; harder to automate.

### Recommendation

**Option A** selected per feedback.

## CLI Design

```bash
ai ps                    # list all managed processes (local + cached Hetzner)
ai ps --refresh          # force SSH re-check of Hetzner (updates cache)
ai ps clean              # show suspect/orphaned, prompt to kill
ai ps clean --force      # kill all orphaned (score ≥ 80) without prompting
```text

**`ai ps` output:**
```text
LOCAL (mac)
  mosh-server   pid=9414   age=27d  score=90  ⚠ orphaned (no client)
  mosh-server   pid=24205  age=0h   score=0   ✓ active (client: artelier)
  signal-watch  pid=33021  age=2h   score=0   ✓ active (project: sw)
  autossh       pid=29914  age=20h  score=0   ✓ active (tunnel: R:9222)
  autossh       pid=62702  age=6d   score=0   ✓ active (tunnel: L:4222)
  circusd       pid=75058  age=5d   score=0   ✓ active

HETZNER (cached 8m ago — run `ai ps --refresh` to update)
  mosh-server   pid=518783 age=0h   score=0   ✓ active (art session)
  nats-server   pid=29579  age=4d   score=0   ✓ active
```text

**`ai ps clean` output:**
```text
Orphaned (score ≥ 80):
  LOCAL  mosh-server  pid=9414   age=27d  score=90  (no active client)
  LOCAL  mosh-server  pid=25170  age=10d  score=85  (no active client)

Suspect (score 40–79):
  LOCAL  sync-watch   pid=44201  age=18h  score=55  (tmux session gone?)

Kill 2 orphan(s)? Suspects require --force to include. [y/N]
```text

**Auto-hygiene (session start + daily cron):**
- Silently kills processes with score ≥ 80 only
- Logs killed PIDs to `~/.local/state/ai-cli/process-hygiene.log`
- Prints single summary line: `[ai ps] Cleaned 2 orphaned process(es). Run 'ai ps' for details.`

## Task Breakdown

### T-01: `ProcessInfo` dataclass + scoring engine

**Size:** M
**Batch:** 1

`ProcessInfo(pid, name, age_seconds, score, verdict, detail, machine)` and the scoring functions per process type.

**Deliverables:**
- `src/ai_cli/process_hygiene.py`
- `ProcessInfo` dataclass
- `score_mosh_server()`, `score_signal_watch()`, `score_watcher()` functions
- `collect_local_processes() → list[ProcessInfo]`
- `collect_remote_processes(use_cache=True) → list[ProcessInfo]` with cache read/write

**Acceptance criteria:**
- [ ] Orphaned mosh-server (no client, age > 6h) scores ≥ 80
- [ ] Active mosh-server (client connected) scores < 40
- [ ] `lsof` unavailable falls back to age-based scoring
- [ ] Remote cache read/write works; stale cache (> TTL) triggers async refresh
- [ ] All scoring functions testable with mocked `ps`/`lsof` output

**Dependencies:** None

### T-02: `ai ps` and `ai ps clean` CLI commands

**Size:** S
**Batch:** 1

**Deliverables:**
- `ai ps` subcommand in `main.py` wired to `process_hygiene.py`
- Formatted table output with score + verdict column
- `--refresh`, `--force` flags
- Confirmation prompt distinguishing orphaned vs suspect

**Acceptance criteria:**
- [ ] `ai ps` lists all processes with age, score, verdict
- [ ] Hetzner results shown from cache with cache age
- [ ] `ai ps clean` prompts before killing orphaned; requires `--force` for suspect
- [ ] `ai ps clean --force` kills orphaned + suspect without prompting
- [ ] `ai ps --refresh` forces SSH re-check and updates cache

**Dependencies:** T-01

### T-03: Auto-hygiene integration

**Size:** S
**Batch:** 1

Wire auto-kill into session start (`ai c` launch) and daily cron.

**Deliverables:**
- Call `auto_clean_orphans()` (kills score ≥ 80 only, logs, prints summary line) at `ai c` session start
- `ai ps cron` command for daily cron invocation (same as auto_clean but also refreshes Hetzner cache)
- `[process_hygiene]` section in `iterm2.toml` or `config.toml`: `auto_clean = true`, `cache_ttl_minutes = 30`, `orphan_threshold = 80`

**Acceptance criteria:**
- [ ] `ai c` launch auto-kills score ≥ 80 processes and logs them
- [ ] No output if 0 orphans found
- [ ] Config flag `auto_clean = false` disables auto-kill
- [ ] `ai ps cron` refreshes remote cache and kills orphans on both machines

**Dependencies:** T-01, T-02

### T-04: Tests

**Size:** S
**Batch:** 1

**Deliverables:**
- `tests/test_process_hygiene.py`
- Scoring unit tests with mocked `ps`/`lsof` output
- Cache read/write tests
- CLI dispatch tests

**Acceptance criteria:**
- [ ] Scoring functions tested for all verdict boundaries (orphaned/suspect/active)
- [ ] `lsof` unavailable fallback tested
- [ ] Cache TTL expiry tested
- [ ] `ai ps clean --force` test verifies correct PIDs passed to `kill()`
- [ ] Remote path mocked (no actual SSH in tests)

**Dependencies:** T-01, T-02, T-03

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03, T-04 | Full feature | Human UAT |

Single batch — all tasks are tightly coupled; no value in shipping detection without clean, or clean without auto.

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Score thresholds, CLI design, auto-hygiene behavior |
| UAT | After Batch 1 | Run `ai ps` live, verify scoring accuracy on real processes |

## Open Questions

1. **Score thresholds:** orphaned at ≥ 80, suspect at 40–79 — do these feel right? Too aggressive or too conservative? Can tune after UAT.

2. **Auto-clean scope:** auto-kill runs on `ai c` session start. Should it also run when `ai c --remote` starts (cleaning Hetzner orphans too), or local-only at launch?

3. **Termius sessions:** mosh-server processes from Termius iOS are tracked the same as any other mosh-server. If you frequently mosh in from Termius and leave sessions open, the age signal needs tuning — Termius sessions can legitimately run for days. Is 24h a reasonable threshold for the age signal, or should it be higher (e.g., 7d)?

4. **Cache location:** `~/.local/state/ai-cli/remote-ps-cache.json` — does this fit with existing XDG state dir usage, or should it go elsewhere?

> **Feedback Round 2:**
> 1. Thresholds fine. How would suspect flagging actually surface to the user?
> 2. Unsure.
> 3. Termius sessions are fleeting — user closes Termius when done. `ai c` sessions from iOS are rare; user resumes from MacBook. Orphaned Termius mosh-servers with persisting tmux sessions are fine to leave as suspect (manual clean).
> 4. (no feedback)
>
> **AI Response Round 2:**
>
> **1 — Suspect flagging:** Two touchpoints only — no intrusive alerts. At session start (`ai c`), orphaned processes (≥ 80) are auto-killed silently and logged. For suspects (40–79), one line is printed: `[ai ps] 1 suspect process(es). Run 'ai ps clean' to review.` The explicit `ai ps` command always shows the full picture with scores. That's it.
>
> **2 — Remote auto-clean:** Recommend yes — also clean Hetzner orphans when launching `ai c --remote`. Already connecting there; zero extra SSH calls needed (cache refreshed as part of the mosh/ssh flow). Local `ai c` cleans local only; `ai c --remote` cleans both.
>
> **3 — Termius scoring confirmed correct:** Disconnected Termius mosh-server scores +50 (no client) +10 (>6h) = 60 suspect, then +20 (>24h) = 70 suspect. Never reaches 80 (orphaned/auto-kill) because the tmux session persists on Hetzner, so "no matching tmux session" (+10) doesn't fire. Correctly becomes a suspect flagged at session start, never auto-killed. No scoring changes needed.
>
> **4 — Cache location:** `~/.local/state/ai-cli/remote-ps-cache.json` consistent with existing XDG usage. Proceeding as specified.
>
> **Feedback Round 3:**
> 3. Termius sessions are fleeting — user closes Termius when done. Don't want to manually kill stale Termius mosh-server orphans. Is there any harm in eventually auto-killing them if we're confident they're stale?
>
> **AI Response Round 3:**
>
> **3 — Termius auto-kill:** No harm — killing a mosh-server only drops the mosh tunnel; the tmux session on Hetzner persists untouched. All work is safe. Updated scoring: added `+5` for "matching tmux session unattached > 2h" and `-10` for "matching tmux session currently attached". Result: Termius orphan with no client, tmux unattached 8h, age 26h = 50+20+10+5 = **85 → auto-kill** (reaches threshold without needing "no tmux session at all"). Active session with tmux attached still scores -10, safely stays **active**.

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-04 | Round 1 approved | Connection-based detection; score-gated auto-clean; remote cache with 30m TTL; new `process_hygiene.py` module; ai-managed scope only |
| 2026-04-04 | Round 2 approved | Suspect flagging: one-line nudge at session start; auto-clean `ai c --remote` cleans both machines; Termius scoring confirmed correct as-is; cache location confirmed |
| 2026-04-04 | Round 3 approved | Termius auto-kill approved: +5 for tmux unattached > 2h; -10 for tmux attached; Termius orphan now reaches 85 → auto-kill after 24h+ |
