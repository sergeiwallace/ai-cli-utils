# Claude Usage Quota Sync — hw-scheduling Integration Plan

**Status:** DRAFT

**Created:** 2026-04-07

**Design:** `docs/designs/claude-usage-telemetry.md`
**Related:** `src/ai_cli/quota.py` — `quota_sync_from_remote()` (shipped 2026-04-07)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

## Table of Contents

- [Overview](#overview)
- [Options](#options)
  - [Option A: Mac worker pulls via SSH](#option-a-mac-worker-pulls-via-ssh)
  - [Option B: Hetzner worker publishes to NATS KV](#option-b-hetzner-worker-publishes-to-nats-kv)
  - [Option C: Event-driven — Hetzner scraper publishes NATS message, Mac subscribes](#option-c-event-driven--hetzner-scraper-publishes-nats-message-mac-subscribes)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

`ai quota sync` (shipped 2026-04-07 in ai-cli-utils) pulls Claude usage snapshots from the
remote Hetzner SQLite DB into the local Mac DB via SSH. There is currently no scheduled
trigger — it must be run manually. This plan wires it into the hw-scheduling system so
Mac and Hetzner CC sessions always reflect current usage in the statusline without
requiring manual invocation.

All four tracked metrics are synced: weekly all-models %, Sonnet-only %, session %, and
extra usage %. Only weekly all-models % is currently displayed in the CC statusline; the
others are stored in local SQLite and accessible via `ai quota status/history`.

The plan also renames the existing `quota_sync` job (which tracks Gemini API cost, not
Claude usage) to `gemini_cost_sync` to eliminate naming ambiguity before adding the new
Claude-specific job.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round 1:**
> - <AI response here>

---

> **Feedback Round 2:**
> - <enter feedback here>

-->

## Options

### Option A: Mac worker pulls via SSH

Job subject: `hw.jobs.mac.claude_quota_sync` (processed by `hw-worker-mac`)

Handler calls `ai quota sync` (subprocess), which SSHes from Mac to Hetzner and upserts
new `quota_snapshots` rows into the local Mac SQLite DB. Interval: every 10 minutes
(matches the Hetzner scraper cadence).

**Pros:**
- Consistent with `git_sync` pattern — Mac worker pulls remote state on schedule
- Zero changes to Hetzner infrastructure or the existing NATS KV schema
- `ai quota status/history/statusline-part` continue reading local SQLite unmodified
- If Mac is offline, jobs buffer in JetStream and are consumed on reconnect
- `ai quota sync` is already shipped, tested, and proven against real SSH

**Cons:**
- One SSH hop per 10-minute cycle (negligible cost; Tailscale latency ~110ms)
- Mac must have Tailscale active or NATS tunnel open for hw-worker-mac to receive the job
  trigger (same requirement as all Mac jobs)

---

### Option B: Hetzner worker publishes to NATS KV

Job subject: `hw.jobs.hetzner.claude_quota_sync` (processed by `hw-worker-hetzner`)

Handler reads the Hetzner SQLite DB and publishes the latest snapshot to NATS KV
(`quota.claude.weekly`). Mac reads from KV via signal-watch or a new poller instead
of from local SQLite.

**Pros:**
- No SSH needed — data flows over existing NATS mesh (Tailscale)
- NATS KV is already used for Gemini quota state; Claude quota fits the same pattern
- Hetzner side is always running; no JetStream buffering concern for the job trigger

**Cons:**
- Requires changes on both sides: Hetzner publishes KV, Mac reads KV
- `quota_statusline_part()`, `quota_status()`, `quota_history()` all read local SQLite —
  all would need updating to read from KV or a KV-to-SQLite sync layer
- Adds a second data path (KV + SQLite) instead of one (SQLite); increases maintenance
- NATS KV holds one value per key — history queries still need local SQLite, so local DB
  can't be eliminated anyway
- Doesn't solve the Mac local DB being stale for `ai quota history`

---

### Option C: Event-driven — Hetzner scraper publishes NATS message, Mac subscribes

Hetzner's `ai quota scrape` publishes a `quota.snapshot` NATS message (already
implemented in `_publish_quota_snapshot()`). Mac's signal-watch (or a new daemon)
subscribes and upserts the snapshot into local SQLite on receipt.

**Pros:**
- Near-real-time: Mac DB updates within seconds of each Hetzner scrape
- No polling interval — no 10-minute lag
- No SSH from Mac to Hetzner; uses existing NATS mesh
- `_publish_quota_snapshot()` already exists in ai-cli-utils; just needs the subscriber side

**Cons:**
- Requires the NATS tunnel (SSH forward) to be running continuously on Mac — currently
  this only runs while a remote transport session is active
- If the NATS tunnel drops (Tailscale flap, Mac sleep), events are missed; no buffering
  for NATS core messages (unlike JetStream)
- More moving parts: requires a running subscriber daemon on Mac, not just a periodic job
- The event-driven path is additive to Option A, not a replacement — Option A's JetStream
  buffering provides reliability that NATS core pub/sub cannot

---

### Recommendation

**Option A**, with Option C as a future enhancement layer.

Option A is the simplest path: one new job, one new handler, no changes to either the
Hetzner side or the local DB read paths. It leverages the JetStream WorkQueue pattern
already used by all other Mac jobs, which provides offline buffering and exactly-once
delivery. Option B introduces unnecessary complexity by creating a second data path that
still doesn't eliminate local SQLite. Option C is appealing for real-time responsiveness
but depends on continuous NATS tunnel availability — acceptable as an enhancement once
signal-watch's NATS connectivity is more stable, but not a reliable foundation today.

> **Feedback Round 1:** Does Option A feel right? Any concern about SSH per cycle vs
> the NATS-native Option B/C?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round 1:**
> - <AI response here>

---

> **Feedback Round 2:**
> - <enter feedback here>

-->

## Task Breakdown

### T-01: Rename `quota_sync` → `gemini_cost_sync`

**Size:** S
**Batch:** 1

Rename the existing Gemini API cost sync job to eliminate confusion with the new Claude
quota sync job. Updates: `jobs.py` (name, subject, registry key), file rename
`handlers/quota_sync.py` → `handlers/gemini_cost_sync.py`, class rename inside.

**Deliverables:**
- `src/hw_scheduling/jobs.py` — updated `JobDef.name`, subject (`hw.jobs.hetzner.gemini_cost_sync`), registry key
- `src/hw_scheduling/handlers/gemini_cost_sync.py` — renamed from `quota_sync.py`, class renamed to `GeminiCostSyncHandler`
- `src/hw_scheduling/handlers/quota_sync.py` — deleted

**Acceptance criteria:**
- [ ] `quota_sync` no longer appears anywhere in `jobs.py`
- [ ] `GeminiCostSyncHandler` in new file; behavior identical to old `QuotaSyncHandler`
- [ ] All existing tests for the handler pass with updated import paths

**Dependencies:** None

---

### T-02: Add `claude_quota_sync` JobDef

**Size:** S
**Batch:** 1

Add the new job entry to `jobs.py`.

**Deliverables:**
- `src/hw_scheduling/jobs.py` — new `JobDef` + `HANDLER_REGISTRY` entry

**Acceptance criteria:**
- [ ] Job name: `claude_quota_sync`
- [ ] Subject: `hw.jobs.mac.claude_quota_sync`
- [ ] Trigger: `{"type": "interval", "minutes": 10}`
- [ ] Handler: `hw_scheduling.handlers.claude_quota_sync.ClaudeQuotaSyncHandler`

**Dependencies:** T-01 (avoids confusion during the same edit)

---

### T-03: Implement `ClaudeQuotaSyncHandler`

**Size:** S
**Batch:** 1

Create `handlers/claude_quota_sync.py`. Calls `ai quota sync` via subprocess and returns
a `JobResult`. The handler is intentionally thin — all sync logic lives in ai-cli-utils.

**Deliverables:**
- `src/hw_scheduling/handlers/claude_quota_sync.py`

**Acceptance criteria:**
- [ ] Resolves `ai` binary via `shutil.which("ai")` with fallback to `~/.local/bin/ai`
- [ ] Calls `subprocess.run(["ai", "quota", "sync"], capture_output=True, text=True, timeout=30)`
- [ ] Returns `JobResult` with stdout summary on success
- [ ] Non-zero exit → `JobResult` with stderr content; does NOT raise
- [ ] Subprocess exception (timeout, FileNotFoundError) → `JobResult` with error message

**Dependencies:** T-02

---

### T-04: Tests for `ClaudeQuotaSyncHandler`

**Size:** S
**Batch:** 1

Unit tests covering success, failure, and binary-not-found paths. Existing
`QuotaSyncHandler` tests remain valid; only the import path changes (covered by T-01).

**Deliverables:**
- New test class `TestClaudeQuotaSyncHandler` in appropriate test file

**Acceptance criteria:**
- [ ] `test_when_ai_quota_sync_succeeds_then_returns_jobresult_with_output`
- [ ] `test_when_ai_quota_sync_returns_nonzero_then_returns_error_jobresult_no_raise`
- [ ] `test_when_subprocess_raises_timeout_then_returns_error_jobresult`
- [ ] `test_when_ai_binary_not_found_then_returns_error_jobresult`
- [ ] All renamed-handler tests pass

**Dependencies:** T-03

---

### T-05: Deploy and live verify

**Size:** S
**Batch:** 2

Deploy updated sergei to Hetzner and Mac, restart hw-clock (new job definition) and
hw-worker-mac (new handler). Verify Mac statusline updates within 10 minutes.

**Deliverables:**
- Updated sergei deployed on Hetzner and Mac
- hw-clock restarted (picks up new `claude_quota_sync` job)
- hw-worker-mac restarted (registers new handler)

**Acceptance criteria:**
- [ ] hw-clock emits `hw.jobs.mac.claude_quota_sync` messages every 10 minutes
- [ ] hw-worker-mac processes the job without error
- [ ] `~/.local/state/ai-cli/quota.db` on Mac gains new rows within 10 minutes
- [ ] `ai quota status` on Mac shows a snapshot timestamp within the last 10 minutes
- [ ] CC statusline on Mac shows updated weekly quota %

**Dependencies:** T-01–T-04

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03, T-04 | Rename + new job + handler + tests | Plan approval (this doc) |
| 2 | T-05 | Deploy + live verify | Human UAT |

> **Feedback Round 1:** Does the batching make sense? T-01–T-04 can be a single autonomous run.
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round 1:**
> - <AI response here>

---

> **Feedback Round 2:**
> - <enter feedback here>

-->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before Batch 1 | Approve scope, option selection, open questions |
| UAT | After Batch 2 | Confirm Mac statusline reflects live quota data |

## Open Questions

1. **Session % — what time frame does it represent?** CC's `/usage` "Current session"
   metric is per-conversation — it resets when you start a new CC conversation, not on
   a daily calendar boundary. There is no separate per-day quota exposed by `/usage`.
   The three time-scoped metrics CC exposes are: session (per-conversation), weekly
   all-models, and weekly Sonnet-only. Should "session %" be tracked and displayed in
   the statusline alongside weekly %? It could be useful as a per-session burn indicator
   (e.g., how much of your weekly budget this one conversation has consumed). Note: if
   you've observed a distinct "daily" sub-limit in the UI that differs from session %,
   flag it — the current regex parser may be missing it.

2. **Statusline secondary metrics:** Currently only weekly all-models % is shown in the
   CC statusline. Should this plan include a follow-on task to expose Sonnet % or
   session % as secondary indicators (e.g., `📊 38% ⚠️ ↑3% | S:21%`)? Or is that
   out of scope here and should be a separate task?

3. **NATS KV publication post-sync:** After Mac pulls new snapshots into local SQLite,
   should it also publish the latest snapshot to NATS KV (`quota.claude.weekly`) so
   other services (Hetzner dashboards, future alerting) can read it without SSH-ing to
   Mac's local DB? The Gemini quota job already uses this pattern (`quota.gemini.monthly`).
   This would be an addition to the handler (Option C enhancement), not a replacement.

4. **Sync trigger on Hetzner scrape:** The 10-minute interval means Mac statusline can lag
   up to 10 minutes behind the Hetzner scraper. Is this acceptable? If real-time
   responsiveness is important, Option C (NATS event subscriber) should be added as a
   follow-on to Option A rather than waiting for the next poll cycle.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- session % — should it be in the statusline? seen a separate daily sub-limit? -->
> 2. <!-- statusline secondary metrics — in scope here or separate? -->
> 3. <!-- NATS KV publication — add to handler or leave for later? -->
> 4. <!-- 10-minute lag acceptable, or should Option C be included? -->
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round 1:**
> - <AI response here>

---

> **Feedback Round 2:**
> - <enter feedback here>

-->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
