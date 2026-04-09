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
> - Scope is right. Proceed.

> **AI Response Round 1:**
> - Scope confirmed. Architecture revised to Option A+C: Hetzner scrapes on schedule (T-00b),
>   publishes `quota.snapshot` NATS core message AND writes `quota.claude.weekly` KV directly
>   (T-00c). Persistent Mac subscriber receives messages real-time (T-00a). 10-min SSH pull
>   (T-02/T-03) is fallback/catch-up only. Session % stored in DB but not displayed in
>   statusline. Weekly all-models % only in statusline. Three new prerequisite tasks added
>   (T-00a, T-00b, T-00c). Key discovery: `_on_quota_snapshot` already exists in signal-watch
>   (`main.py:2692`) — T-00a extracts it into a persistent Circus daemon rather than writing
>   from scratch.

---

> **Feedback Round 2:**
> - <enter feedback here>

---

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

**Option C primary + Option A fallback.**

Option C is the primary path: Hetzner publishes `quota.snapshot` after each scrape;
a persistent Mac Circus daemon subscribes and upserts into local SQLite in real-time.
`_on_quota_snapshot` already exists in signal-watch (`main.py:2692`) — it only needs
to be extracted into a standalone persistent process (T-00a) rather than built from
scratch. Option A (10-min hw-scheduling SSH pull) runs as a catch-up layer, filling
gaps during Mac NATS tunnel outages or missed messages. Option B is rejected — NATS KV
as the read path creates a second data path without eliminating local SQLite.

**Hetzner KV ownership (OQ-3):** `_publish_quota_snapshot()` is extended (T-00c) to
also write `quota.claude.weekly` KV directly after each scrape. Mac does not re-publish
to KV — Hetzner owns the data and Hetzner writes it.

> **Feedback Round 1:** Does Option A feel right? Any concern about SSH per cycle vs
> the NATS-native Option B/C?
> - Option A + C. Want real-time (Option C is primary). Option A as fallback/catch-up.

> **AI Response Round 1:**
> - Revised to Option C primary + Option A fallback. Three prerequisites added:
>   - **T-00a**: Extract `_on_quota_snapshot` (already at `main.py:2692`) into a persistent
>     Circus-managed Mac daemon. This is the Option C subscriber side — largely written already.
>   - **T-00b**: Schedule `ai quota scrape` on Hetzner via a new hw-worker-hetzner job
>     (`claude_quota_scrape`). Currently no periodic scrape exists — the scraper only runs
>     when manually invoked or when a CC session loads on Hetzner.
>   - **T-00c**: Extend `_publish_quota_snapshot()` to write `quota.claude.weekly` NATS KV
>     directly on Hetzner after each scrape. Hetzner owns the data; no Mac round-trip.
> - Option A (T-02/T-03) becomes the fallback: 10-min hw-scheduling pull via SSH catches up
>   any messages missed during Mac tunnel outages.

---

> **Feedback Round 2:**
> - <enter feedback here>

---

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round 1:**
> - <AI response here>

---

> **Feedback Round 2:**
> - <enter feedback here>

-->

## Task Breakdown

### T-00a: Persistent Mac quota snapshot subscriber

**Size:** M
**Batch:** 1

Extract `_on_quota_snapshot` from `signal-watch` (`main.py:2692`) into a standalone
`ai internal quota-subscriber` daemon. Register as a persistent Circus watcher on Mac
so it runs continuously regardless of CC session lifecycle. Subscribes to
`quota.snapshot` NATS core subject; calls `record_quota_snapshot()` on receipt.

**Deliverables:**
- `src/ai_cli/main.py` — `ai internal quota-subscriber` command entry point
- Mac Circus config — new watcher entry for the subscriber daemon

**Acceptance criteria:**
- [ ] Daemon subscribes to `quota.snapshot` on startup
- [ ] Calls `record_quota_snapshot()` on each received message
- [ ] Runs as persistent Circus watcher; survives CC session exits
- [ ] Reconnects on NATS disconnect (uses existing `NATSClient` retry/reconnect)
- [ ] `signal-watch` no longer registers the `quota.snapshot` subscription (deduplication)

**Dependencies:** None

---

### T-00b: Schedule Hetzner quota scrape

**Size:** S
**Batch:** 1

Add a `claude_quota_scrape` job to hw-scheduling so Hetzner runs `ai quota scrape`
every 10 minutes via `hw-worker-hetzner`. Currently no periodic scrape exists on
Hetzner — the scraper only runs when manually invoked.

**Deliverables:**
- `src/hw_scheduling/jobs.py` — new `claude_quota_scrape` `JobDef`
  (subject: `hw.jobs.hetzner.claude_quota_scrape`, interval: 10 min)
- `src/hw_scheduling/handlers/claude_quota_scrape.py` — calls `ai quota scrape` via
  subprocess; returns `JobResult`

**Acceptance criteria:**
- [ ] hw-clock emits `hw.jobs.hetzner.claude_quota_scrape` every 10 minutes
- [ ] Handler calls `ai quota scrape` and returns `JobResult` with stdout/stderr
- [ ] Non-zero exit → error `JobResult`; does not raise
- [ ] Scrape triggers `_publish_quota_snapshot()` (already wired in `quota_scrape()`)

**Dependencies:** None

---

### T-00c: Hetzner writes `quota.claude.weekly` NATS KV after each scrape

**Size:** S
**Batch:** 1

Extend `_publish_quota_snapshot()` in `quota.py` to also write `quota.claude.weekly`
NATS KV immediately after publishing the NATS core message. Hetzner owns the data and
writes it directly — Mac does not re-publish.

**Deliverables:**
- `src/ai_cli/quota.py` — KV write added to `_publish_quota_snapshot()`
- KV value: JSON with `usage_percent`, `weekly_sonnet_pct`, `session_pct`, `extra_pct`,
  `snapshotted_at`

**Acceptance criteria:**
- [ ] `quota.claude.weekly` KV updated on every successful scrape
- [ ] KV write happens in the same call as the NATS core publish
- [ ] Mac subscriber does NOT write to this KV key

**Dependencies:** None

---

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

### T-02: Add `claude_quota_sync` JobDef (fallback catch-up)

**Size:** S
**Batch:** 2

Add the fallback/catch-up job to `jobs.py`. This job runs on Mac via `hw-worker-mac`
every 10 minutes and pulls snapshots from Hetzner via SSH — it covers gaps during NATS
tunnel outages where T-00a's real-time subscriber may have missed messages.

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
**Batch:** 2

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
**Batch:** 2

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
| 1 | T-00a, T-00b, T-00c | Prerequisites: persistent subscriber + Hetzner scrape schedule + KV write | Plan approval (this doc) |
| 2 | T-01, T-02, T-03, T-04 | Rename gemini handler + fallback SSH-pull job + handler + tests | Batch 1 complete |
| 3 | T-05 | Deploy + live verify | Human UAT |

> **Feedback Round 1:** Does the batching make sense? T-01–T-04 can be a single autonomous run.
> - Batching is fine. Will revisit after open questions are resolved and plan is updated.

> **AI Response Round 1:**
> - Revised to 3 batches. Batch 1 (T-00a, T-00b, T-00c) covers prerequisites that span
>   both ai-cli-utils and hw-scheduling. Batch 2 (T-01–T-04) is the original rename +
>   fallback job work — still a single autonomous run. Batch 3 (T-05) is deploy + UAT.

---

> **Feedback Round 2:**
> - <enter feedback here>

---

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
| Batch 1 complete | After Batch 1 | Confirm persistent subscriber + Hetzner scrape working before adding fallback layer |
| UAT | After Batch 3 | Confirm Mac statusline reflects live quota data in real-time |

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
> 1. Session % — not in statusline. Include in scraper and DB for potential future use, but don't display.
> 2. Weekly all-models % only in statusline. No secondary metrics.
> 3. NATS KV should be updated by Hetzner directly after each scrape — not by Mac after pulling. Having Mac pull then re-publish to Hetzner's KV is a round trip that makes no sense. Hetzner owns the data; Hetzner should write it to KV.
> 4. Want real-time. Option C is primary path.

> **AI Response Round 1:**
> 1. Confirmed. `quota_scrape()` continues to capture `session_pct` and store it in
>    `quota_snapshots.session_pct`. `quota_statusline_part()` is unchanged — weekly % only.
> 2. Confirmed. No changes to statusline display beyond what's already there.
> 3. Agreed. T-00c adds a KV write to `_publish_quota_snapshot()` on Hetzner immediately
>    after the NATS core publish. Mac's subscriber (T-00a) is read-only — it calls
>    `record_quota_snapshot()` only; no KV writes.
> 4. Option C is primary (T-00a). Option A is fallback. Architecture revised accordingly.

---

> **Feedback Round 2:**
> - <enter feedback here>

---

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
| 2026-04-08 | Round 1 | Scope approved. Option C primary + Option A fallback (not A-only). Session % in DB, not statusline. Weekly all-models % only. Hetzner writes KV directly (no Mac round-trip). Real-time via persistent subscriber. Three prerequisite tasks added: T-00a (Mac subscriber), T-00b (Hetzner scrape schedule), T-00c (KV write). |
