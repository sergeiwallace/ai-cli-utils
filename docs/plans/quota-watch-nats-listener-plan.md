---
title: quota_watch NATS Listener — Implementation Plan
category: plan
tags: [quota, nats, quota-watch, ai-cli-57]
status: implemented
source: claude-sonnet-4-6
---

# quota_watch NATS Listener — Implementation Plan

**Status:** IMPLEMENTED

**Created:** 2026-04-29

**Task:** `[AI-CLI-57]`

**Design:** `docs/designs/claude-usage-telemetry.md`

**Related (aido):**
- `aido/docs/designs/research-graph-v2.md` — E6 mid-run quota visibility; specifies `quota.scrape.request.{machine}` trigger protocol
- `aido/docs/plans/mid-run-quota-monitor-plan.md` — T-04 NATS scrape trigger + KV reader (consumer of this listener)
- `aido/docs/plans/research-platform-implementation-plan.md` — cross-dependency tracking for AIDO-48 T-04

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
- [Decisions](#decisions)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Implementation Audit](#implementation-audit)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

`quota_watch` is the Circus-managed daemon that polls Claude usage and fires
threshold alerts. This plan adds four NATS-based capabilities: an on-demand
scrape trigger (so aido's quota monitor can request a fresh scrape without
waiting for the poll interval), a machine-suffixed KV key for multi-machine
disambiguation, a scrape-completion ack, and a 60-second heartbeat. Together
these unblock AIDO-48 T-04 (aido mid-run quota monitor).

**Note: all four items are already fully implemented and tested as of
2026-04-29.** This doc is retrospective — it captures scope, decisions, and
acceptance criteria for the record.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Decisions

### Decision Summary

| # | Decision | Options | Status |
|---|----------|---------|--------|
| D1 | Listener threading model | (a) daemon thread in quota_watch, (b) separate Circus worker | `APPROVED — (a)` |
| D2 | KV key migration | (a) hard rename, (b) machine-conditional with fallback | `APPROVED — (b)` |

### D1: Listener threading model — `[APPROVED: (a) daemon thread in quota_watch]`

#### (a) Daemon thread inside quota_watch

Start `_run_nats_quota_listener` in a `daemon=True` thread from within
`quota_watch`. Thread owns its own asyncio event loop, stops via a
`threading.Event` when `quota_watch` exits.

**Pros:**
- Single Circus worker — no new process to manage
- Inherits `quota_watch` lifecycle naturally (start/stop together)
- Simpler deployment

**Cons:**
- Thread shares address space — a crash in the listener can affect the watcher
- Two asyncio loops (quota_watch has its own for NATS publish, listener has another)

#### (b) Separate Circus worker

Add a second `[watcher]` entry in `circus.ini` running `ai quota listen`.

**Pros:**
- Complete isolation
- Circus handles restarts independently

**Cons:**
- New CLI subcommand required
- Two workers to manage per machine
- More complex deployment and config

#### Recommendation

> **Decision:** `APPROVED — (a) daemon thread in quota_watch`

A separate worker is not justified — the listener is lightweight (subscribe +
heartbeat loop, minimal CPU) and tightly coupled to the watch lifecycle. Daemon
thread keeps the deployment surface minimal.

---

### D2: KV key migration — `[APPROVED: (b) machine-conditional with fallback]`

#### (a) Hard rename — always `quota.claude.current.{machine}`

Require `AI_CLI_HOST` to be set; fail loudly if absent. Drop the bare
`quota.claude.current` key entirely.

**Pros:**
- Simpler — one key, one read path
- Forces correct configuration

**Cons:**
- Breaks external pip users who haven't set `AI_CLI_HOST`
- No graceful degradation

#### (b) Machine-conditional with fallback

Write/read `quota.claude.current.{machine}` when `AI_CLI_HOST` is set;
fall back to legacy `quota.claude.current` when unset.

**Pros:**
- Backwards compatible for external users
- Multi-machine disambiguation works for all managed machines

**Cons:**
- Two code paths to maintain

#### Recommendation

> **Decision:** `APPROVED — (b) machine-conditional with fallback`

Pre-v1 public package — external pip users may not have `AI_CLI_HOST`. The
fallback costs one conditional per read/write and avoids a breaking change.
Can hard-rename at v1.0.

## Task Breakdown

### T-01: NATS subscription + on-demand scrape trigger

**Size:** M
**Batch:** 1

Add `_run_nats_quota_listener(machine, *, stop_event)` daemon thread to
`quota_watch`. Subscribes to `quota.scrape.request.{machine}`. On message,
checks `_SCRAPE_LOCK_PATH` for in-flight dedup; if clear, calls
`_launch_background_scrape()`.

**Deliverables:**

- `src/ai_cli/quota.py` — `_run_nats_quota_listener()` function
- `src/ai_cli/quota.py` — `quota_watch()` starts listener thread when `AI_CLI_HOST` set

**Acceptance criteria:**

- [x] `quota_watch` starts a daemon thread named `nats-quota-listener` when `AI_CLI_HOST` is set
- [x] Thread is NOT started when `AI_CLI_HOST` is unset
- [x] Subscription on `quota.scrape.request.{machine}` — message fires `_launch_background_scrape()`
- [x] No scrape launched when `_SCRAPE_LOCK_PATH` exists (dedup)
- [x] Thread exits cleanly when `stop_event` is set

**Dependencies:** None

---

### T-02: KV key rename to `quota.claude.current.{machine}`

**Size:** S
**Batch:** 1

Update all KV reads and writes in `_publish_quota_snapshot()` and
`_try_read_kv_snapshot()` to use `quota.claude.current.{machine}` when
`AI_CLI_HOST` is set, falling back to bare key when unset.

**Deliverables:**

- `src/ai_cli/quota.py` — write path: `_publish_quota_snapshot()`
- `src/ai_cli/quota.py` — read path: `_try_read_kv_snapshot()`

**Acceptance criteria:**

- [x] Write path uses `quota.claude.current.{machine}` when `AI_CLI_HOST` set
- [x] Write path uses `quota.claude.current` (bare) when `AI_CLI_HOST` unset
- [x] Read path uses `quota.claude.current.{machine}` when `AI_CLI_HOST` set
- [x] Read path uses `quota.claude.current` (bare) when `AI_CLI_HOST` unset
- [x] When machine is set, bare key is NOT written (no double-write)

**Dependencies:** None

---

### T-03: Scrape-completion ack

**Size:** S
**Batch:** 1

After writing the snapshot KV entry, write a second KV entry to
`hw_state[quota.scrape.ack.{machine}]` with `{"scraped_at": <unix_ts>}`.
Only when `machine` is non-empty.

**Deliverables:**

- `src/ai_cli/quota.py` — ack write in `_publish_quota_snapshot()`

**Acceptance criteria:**

- [x] `quota.scrape.ack.{machine}` written after each snapshot publish
- [x] Ack payload contains `scraped_at` float timestamp
- [x] No ack written when machine is empty

**Dependencies:** T-02 (KV write path already open)

---

### T-04: 60-second heartbeat

**Size:** S
**Batch:** 1

In the `_run_nats_quota_listener` loop, publish to
`hw_state[quota_watch.heartbeat.{machine}]` every 60 seconds with
`{"ts": <unix_ts>}`.

**Deliverables:**

- `src/ai_cli/quota.py` — heartbeat write in `_run_nats_quota_listener()`

**Acceptance criteria:**

- [x] Heartbeat written to `quota_watch.heartbeat.{machine}` at ~60s intervals
- [x] Heartbeat payload contains `ts` float timestamp
- [x] Heartbeat only fires when JetStream is available (`client.js` is truthy)

**Dependencies:** T-01 (listener thread running)

---

### T-05: Tests

**Size:** M
**Batch:** 1

Unit tests for all four items above, covering happy path and failure modes.

**Deliverables:**

- `tests/test_quota.py` — new test classes for listener, KV key, ack, heartbeat

**Acceptance criteria:**

- [x] `quota_watch` starts listener thread when `AI_CLI_HOST` set
- [x] `quota_watch` does NOT start listener when `AI_CLI_HOST` unset
- [x] Scrape triggered on subscription message
- [x] Scrape NOT triggered when lock file exists
- [x] Heartbeat key written correctly
- [x] KV key is suffixed when machine set, bare when unset
- [x] No bare key written when machine is set
- [x] Ack written after snapshot with correct payload
- [x] No ack written when machine unset

**Dependencies:** T-01–T-04

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03, T-04, T-05 | All four NATS capabilities + tests | UAT |

All tasks are independent of each other's deliverables (T-03/T-04 share the
listener loop but don't depend on each other's logic) — implemented as a
single batch.

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Implementation Audit

> **Step 14 gate** — complete before updating docs or presenting UAT.

### T-01: NATS subscription + scrape trigger

- [x] `_run_nats_quota_listener()` exists in `quota.py`
- [x] `quota_watch()` starts daemon thread when `AI_CLI_HOST` set
- [x] Thread NOT started when `AI_CLI_HOST` unset
- [x] Subscription on `quota.scrape.request.{machine}`
- [x] Dedup via `_SCRAPE_LOCK_PATH.exists()`

### T-02: KV key rename

- [x] Write: `f"quota.claude.current.{machine}" if machine else "quota.claude.current"`
- [x] Read: same conditional in `_try_read_kv_snapshot()`
- [x] Bare key not written when machine is set

### T-03: Ack

- [x] `quota.scrape.ack.{machine}` written in `_publish_quota_snapshot()`
- [x] Payload: `{"scraped_at": time.time()}`
- [x] Guarded by `if machine:`

### T-04: Heartbeat

- [x] `quota_watch.heartbeat.{machine}` written in `_run_nats_quota_listener()`
- [x] 60s interval via `heartbeat_interval = 60.0`
- [x] Guarded by `client.js` check

### T-05: Tests

- [x] All 9 ACs have corresponding tests in `tests/test_quota.py`
- [x] Tests pass (verified 2026-04-29)

**Audit completed:** 2026-04-29

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope and approach |
| UAT | After implementation | Approve for merge |

## Open Questions

1. Should `AI_CLI_HOST` being unset be a hard error in a future v1.0 (enabling the hard rename)? Currently silently falls back to bare key.
2. The listener thread has no reconnect logic — if NATS goes down mid-session, the thread exits silently. Should it retry?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response -->
> 2. <!-- Response -->
> - <enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-29 | Plan created retrospectively | Implementation already complete; doc captures decisions and ACs |
