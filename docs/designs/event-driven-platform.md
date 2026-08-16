---
title: "Event-Driven Platform Architecture"
category: design
tags: [event-driven, nats, pub-sub, messaging, fleet, sync, architecture, platform]
status: approved
source: myproject
---

> **Migrated from `myproject` (SW-837, 2026-07-26).** This design doc followed its implementation
> into this repo: the code it describes lives here, not in `myproject`, since the SW-907 repo-ownership
> migration. `myproject/docs/designs/event-driven-platform.md` is now a `status: moved` stub pointing here.
> Content is unchanged by the move — any drift between this doc and the current code predates it
> and is not a migration artifact.

# Event-Driven Platform Architecture — Design Document

**Status:** APPROVED

**Created:** 2026-03-24

**Approved:** 2026-03-25

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
- [Architecture](#architecture)
- [Subject Hierarchy](#subject-hierarchy)
- [Candidate Systems — Event-Driven Migration Assessment](#candidate-systems--event-driven-migration-assessment)
- [Systems NOT Recommended for Event-Driven](#systems-not-recommended-for-event-driven)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Problem Statement

The ai-core platform has adopted "event-driven over polling" as a core design principle, but most subsystems still use polling or manual triggers. Concrete symptoms: `ai sync push` requires a manual follow-up `ai sync pull` on the remote machine; fleet heartbeats exist in design but aren't wired; session lifecycle signals are implicit; telemetry and quota tracking don't exist. Each of these is solved independently by the same infrastructure: a NATS message bus with a consistent subject hierarchy.

This document defines the platform-wide event-driven architecture — what NATS subjects exist, which systems should publish/subscribe, which systems should stay polling/cron, and the phased rollout order.

**Prior decisions already made (do not re-research):**
- NATS selected over Redis as platform event bus — see `docs/research/message-broker-evaluation.md`
- NATS Nervous System architecture approved — see `docs/designs/fleet-management.md`
- NATS JetStream from day 1 — decided 2026-03-25; see Decision 2 deep-dive
- Intra-machine only initially (localhost:4222); cross-machine events via staging repo until a shared NATS cluster exists

---

## Design Decisions

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Subject naming scheme | (a) flat `verb.noun` (b) `domain.entity.verb` (c) `domain.entity.id.verb` | **(c) `domain.entity.id.verb`** | Enables per-entity subscriptions (`fleet.worker.sw-1.*`), wildcards (`fleet.worker.*.heartbeat`), and domain isolation. Matches existing `fleet.worker.{id}.heartbeat` design. | Approved (fleet-management.md) |
| 2 | NATS tier | (a) Core only (b) JetStream from day 1 (c) Core now, JetStream when needed | **(b) JetStream from day 1** | 4 of 14 candidate systems already require durable delivery; building 10+ integrations on Core creates a guaranteed migration. JetStream is a superset — Core-style pub/sub still works unchanged. Config overhead is a one-time cost. See Decision 2 deep-dive. | **Decided 2026-03-25** |
| 3 | Cross-machine events | (a) Shared NATS cluster (b) NATS Cloud (c) Staging repo as transport (d) SSH exec | **(c) Staging repo as transport for now** | No shared network between Mac and Hetzner without VPN/tunnel. Staging repo already exists. NATS handles intra-machine delivery; staging repo handles cross-machine. Revisit when VPN or persistent tunnel is in place. | **Approved 2026-03-25** |
| 4 | Telemetry storage | (a) SQLite WAL (b) External pipeline (Segment, PostHog) | **(a) SQLite WAL** | JetStream is the delivery transport, not a storage option — removed. SQLite: zero dependencies, full SQL, already in platform, works offline, no cost. External pipeline warranted at ~1,000 MAU. Pattern: UI/CLI → JetStream stream → background writer → SQLite. See `docs/research/telemetry-event-design-early-stage-apps.md`. | **Approved 2026-03-25** |
| 5 | What NOT to event-drive | (a) Everything (b) Only real-time needs (c) Assess per system | **(c) Assess per system** | Some systems are inherently time-triggered (daily digest, health checks) and should remain cron. Event-driven is for reactive patterns, not scheduled ones. See §Systems NOT Recommended. | **Approved 2026-03-25** |

### Decision 2 deep-dive: JetStream now vs. later

**What JetStream adds over NATS Core:**
NATS Core is pure pub/sub — fire-and-forget. If no subscriber is listening when a message is published, it's gone. JetStream layers persistence on top: durable delivery, consumer ACKs (unacked messages are redelivered), work queues with exactly-once semantics, replay from offset, and pull consumers that request batches at their own pace.

**Decision: JetStream from day 1** (decided 2026-03-25)
<!-- decision-record: chosen-option=(b); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

Key factors that drove the decision:
- 4 of 14 candidate systems already require durable delivery (auto-dream safety, quota tracking, mobile notifications, session teleportation) — JetStream adoption was never truly optional
- Building 10+ pub/sub integrations in one batch on Core would guarantee a migration; the migration cost compounds with every additional publisher/subscriber
- JetStream is a strict superset — fire-and-forget subjects behave identically; no downside for ephemeral events like heartbeats
- The "killer app" P3 features (session teleportation, mobile notifications) are load-bearing on durable delivery; retrofitting JetStream at P3 would slow exactly the work that matters most
- Config overhead (`jetstream: {}` + storage dir) is a one-time cost, not ongoing complexity

**Use cases by delivery guarantee needed:**

| Use case | Guarantee needed | Notes |
|---|---|---|
| Heartbeats (`fleet.worker.{id}.heartbeat`) | None — ephemeral | Next heartbeat arrives in 30s; loss acceptable |
| Sync trigger (`sync.pull.requested`) | At-least-once | Manual pull as fallback, but durable is cleaner |
| Session lifecycle events | At-least-once | Dashboard fallback is git log |
| auto-dream sync safety | **At-least-once** | Safety-critical; missed = unsafe sync during dream write |
| Quota threshold alerts | **At-least-once** | Subscriber may be offline; can't miss a threshold crossing |
| aido work queues | **Exactly-once** | Lost task = silent dropped work |
| Session teleportation | **At-least-once** | Missed handoff = broken feature, not degraded |
| Mobile notifications | **At-least-once** | Termius frequently offline |

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. Subject naming: Approved
> 2. NATS tier: Approved
> 3. Cross-machine: Approved. add a follow-up task to look into the other two options at a future date when relevant
> 4. Telemetry storage:
>
>    - does this change now that we are implementing jetstream from the start?
> 5. Scope boundary: approved
> - <enter feedback here>

> **AI Response Round 1:**
> 1–3, 5: Noted. Follow-up task added to roadmap: evaluate shared NATS cluster / Tailscale tunnel for cross-machine delivery when staging repo becomes a bottleneck or P3 (mobile/teleportation) requires it.
> 4: JetStream doesn't change the storage decision. JetStream is the delivery transport — telemetry events flow through a JetStream stream; SQLite is the persistent store for analytics/querying. They're separate concerns. JetStream has time/size-based retention limits, not designed for long-term history. The pattern stays: publisher → JetStream stream → background writer consumer → SQLite. Decision 4 updated to clarify this and marked approved.

---

> **Feedback Round 2:** Your approval/feedback on each decision:
> 4. Telemetry storage: SQLite WAL confirmed. JetStream is not a storage option — remove it. Options are SQLite WAL vs external pipeline only.

> **AI Response Round 2:**
> Decision 4 updated: JetStream removed as option, options renumbered (a) SQLite WAL / (b) External pipeline. Rationale clarifies JetStream is the delivery transport, not the store.

---

> **Feedback Round 3:**
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

---

## Architecture

```mermaid
%%{init: {'theme': 'dark'}}%%
graph LR
    subgraph Mac["💻 Mac"]
        SyncPush["ai sync push"]
        CCHooksMac["CC hooks"]
        AiCoreMac["ai-core"]
        AiCLI["ai-cli"]
    end

    subgraph Hetzner["🖥 Hetzner"]
        Engine["engine script"]
        CCHooksHetzner["CC hooks"]
        Aido["aido"]
        SyncWatch["ai sync watch"]
    end

    subgraph NATS["⚡ NATS JetStream (localhost:4222)"]
        SyncSubj["sync.pull.requested"]
        HeartbeatSubj["fleet.worker.{id}.heartbeat"]
        FleetEventSubj["fleet.worker.{id}.event"]
        SessionSubj["session.{id}.started / stopped"]
        DreamSubj["memory.dream.started / completed"]
        TaskSubj["task.updated"]
        QuotaSubj["quota.threshold.{pct}"]
        AidoSubj["aido.run.started / completed"]
    end

    subgraph Subscribers["📡 Subscribers"]
        FleetUI["fleet dashboard"]
        NotifMgr["notification manager"]
        CurationEngine["curation engine"]
        Digest["digest / telemetry"]
        SyncGuard["sync push guard"]
    end

    SyncPush -->|publishes| SyncSubj --> SyncWatch
    Engine -->|every 30s| HeartbeatSubj --> FleetUI
    Engine -->|START/STOP/SIGNAL| FleetEventSubj --> FleetUI
    CCHooksMac -->|on open/close| SessionSubj --> FleetUI
    CCHooksHetzner -->|on open/close| SessionSubj
    CCHooksMac -->|auto-dream| DreamSubj --> SyncGuard
    AiCoreMac -->|task changes| TaskSubj --> CurationEngine
    AiCLI -->|quota events| QuotaSubj --> NotifMgr
    Aido -->|run lifecycle| AidoSubj --> Digest

    subgraph CrossMachine["🔄 Cross-machine transport (until shared NATS cluster)"]
        direction LR
        MacNode["Mac"] -->|"ai sync push → git staging repo"| HetznerNode["Hetzner"]
        HetznerNode -->|"ai sync watch pulls on event"| HetznerNode
    end
```text

### Component Roles

**NATSClient** (`ai-cli: src/ai_cli/messaging.py`):
- Single async wrapper used by all ai-cli publishers and subscribers
- `publish(subject, data)` — fire-and-forget, non-fatal if NATS down
- `subscribe(subject, callback)` — blocking, used by `ai sync watch` and fleet watcher

**`ai sync watch`** (`ai_cli/sync.py`):
- Long-lived subscriber: `sync.pull.requested` → `sync_pull(["--force"])`
- Run in background tmux pane on server at session start
- Exit 1 if NATS unavailable at startup (non-fatal; sync still works manually)

**Fleet engine script** (`ai_cli/main.py` — `get_engine_script`):
- Already publishes `fleet.worker.{id}.heartbeat` every 30 loop iterations
- Will publish `fleet.worker.{id}.event` on START/STOP/SIGNAL

**Notification manager** (`ai_cli/notifications.py`):
- Downstream subscriber for quota thresholds, human-gate events
- OS notification via `osascript` (Mac) or `notify-send` (Linux)

---

## Subject Hierarchy

`{domain}.{entity}.{id}.{verb}` — where `id` is omitted for non-instance subjects.

### Current (implemented)

| Subject | Publisher | Subscriber | Description |
|---------|-----------|------------|-------------|
| `fleet.worker.{id}.heartbeat` | engine script (every 30s) | fleet dashboard (SW-501) | Worker liveness + CPU/mem |
| `fleet.worker.{id}.event` | engine script | fleet dashboard, notification manager | START, STOP, SIGNAL, FAILED |
| `sync.pull.requested` | `ai sync push` | `ai sync watch` | Trigger cross-machine pull |

### Phase 2 — Session Lifecycle

| Subject | Publisher | Subscriber | Description |
|---------|-----------|------------|-------------|
| `session.{id}.started` | CC Start hook | fleet dashboard, digest | Session opened |
| `session.{id}.stopped` | CC Stop hook | fleet dashboard, digest | Session closed |
| `session.{id}.compacted` | `/compact` hook | fleet dashboard | Context compacted |

### Phase 3 — Memory / Sync Safety

| Subject | Publisher | Subscriber | Description |
|---------|-----------|------------|-------------|
| `memory.dream.started` | auto-dream hook (when available) | `ai sync push` guard | Dream write in progress — block sync push |
| `memory.dream.completed` | auto-dream hook | `ai sync push` | Resume sync push |
| `memory.learning.stored` | ai-core MCP `store_learning` | other CC sessions (optional refresh) | New learning available (SW-618) |

### Phase 4 — Telemetry & Quota

| Subject | Publisher | Subscriber | Description |
|---------|-----------|------------|-------------|
| `quota.threshold.50` | quota tracker (SW-613) | notification manager | Usage at 50% |
| `quota.threshold.75` | quota tracker | notification manager | Usage at 75% |
| `quota.threshold.90` | quota tracker | notification manager | Usage at 90% — slow down |
| `telemetry.action.{type}` | web UI / CLI | SQLite telemetry writer (SW-16) | User behavior event |

### Phase 5 — Cross-Project / aido

| Subject | Publisher | Subscriber | Description |
|---------|-----------|------------|-------------|
| `aido.run.started` | aido CLI | digest, telemetry | Research run begun |
| `aido.run.completed` | aido CLI | digest, telemetry | Research run done (cost, summary) |
| `task.updated` | ai-core MCP write tools | curation engine (SW-13) | Re-run curation on task change |
| `health.check.completed` | health cron | digest | Test suite result available |

---

## Candidate Systems — Event-Driven Migration Assessment

The table below assesses every current and planned platform system against the event-driven pattern. "Adopt" = replace or augment polling/manual with NATS pub/sub. JetStream is the baseline (Decision 2); the "JetStream?" column notes whether durable delivery / ACKs are required or if fire-and-forget on JetStream is sufficient.

| # | System | Current Pattern | Adopt Event-Driven? | What Changes | Phase | SW | Built? | JetStream? |
|---|--------|----------------|---------------------|--------------|-------|----|--------|------------|
| 1 | `ai sync pull` trigger | Manual / cron | **Yes** | `ai sync push` publishes `sync.pull.requested`; `ai sync watch` subscribes | ✅ Done | SW-614 | ✅ Built | No — manual pull is fallback; loss acceptable |
| 2 | Fleet heartbeats | Designed, not wired | **Yes** | Wire existing engine script → `fleet.worker.{id}.heartbeat` on NATS | 2 | SW-501 | 🔶 Partial (designed, not wired) | No — ephemeral by nature; next heartbeat arrives in 30s |
| 3 | Fleet worker events | Designed, not wired | **Yes** | Engine script publishes START/STOP/SIGNAL; dashboard subscribes | 2 | SW-501 | 🔶 Partial (designed, not wired) | Maybe — START/STOP could be valuable to persist; SQLite can cover this instead |
| 4 | Session lifecycle | Implicit (no signal) | **Yes** | CC Start/Stop hooks publish `session.{id}.started/stopped` | 2 | SW-642 | ❌ Not built | No — git log is source of truth for session history |
| 5 | auto-dream sync safety | No guard exists | **Yes** | `memory.dream.started/completed` gates `ai sync push` (SW-644) | 3 | SW-644 | ❌ Not built | **Yes** — safety-critical; missed event = sync proceeds unsafely during dream write |
| 6 | Shared memory notifications | Not built yet | **Yes (notification layer only)** | `memory.learning.stored` nudges other sessions to refresh; SQLite is still the store | 5 | SW-618 | ✅ Built | No — ephemeral nudge; SQLite is the durable store |
| 7 | Claude quota tracking | Not built yet | **Yes** | Quota poller writes thresholds as NATS events → notification manager | 4 | SW-613 | ❌ Not built | **Yes** — quota threshold alerts shouldn't be missed; late subscriber needs to catch up |
| 8 | User behavior telemetry | Not built yet | **Yes (write path only)** | UI/CLI publishes `telemetry.action.*`; background thread batches to SQLite | 4 | SW-16 | ❌ Not built | No — SQLite batch writer handles durability; Core is fine for the fan-out |
| 9 | aido run visibility | JSONL only | **Yes (bridge)** | aido CLI publishes `aido.run.started/completed` on NATS; digest/telemetry subscribe | 5 | SW-647 | ✅ Built | No — JSONL already persists run history; NATS is observability only |
| 10 | Task update → curation | Cron-only (staleness) | **Yes (additive)** | ai-core MCP write tools publish `task.updated`; curation engine reacts immediately | 5 | SW-13 | ✅ Built | Maybe — if curation engine is offline, missed events fall back to cron scan anyway |
| 11 | Health check results | Cron | **Yes (output only)** | Cron remains as trigger; publishes `health.check.completed` on finish for digest | 5 | SW-539 | ✅ Built | No — output event is nice-to-have; digest can poll SQLite results instead |
| 12 | Fleet mobile notifications | Not built | **Yes** | Downstream subscriber to fleet events; push via Termius | P3 | SW-609 | ❌ Not built | **Yes** — Termius may not be connected; durable delivery ensures notification isn't dropped |
| 13 | Session teleportation | Not built | **Yes** | `session.teleport.requested` event initiates handoff protocol | P3 | SW-610 | ❌ Not built | **Yes** — handoff protocol requires reliable delivery; fire-and-forget is unsafe here |
| 14 | Fleet Web UI | Not built | **Yes** | Browser subscribes to NATS WebSocket gateway for live updates | P3 | SW-611 | ❌ Not built | No — browser reconnects on page load; live updates are ephemeral by nature |

---

## Systems NOT Recommended for Event-Driven

| # | System | Current Pattern | Recommendation | Reasoning |
|---|--------|----------------|----------------|-----------|
| 1 | **Daily digest compilation** (SW-31) | `cron` | **Keep cron** | Digest is time-triggered by definition ("daily at session start"). The cron *trigger* is correct. Individual inputs (curation, guidance) may fire events, but the aggregation step should remain scheduled. **Pros of event-driven:** compile immediately when tasks change. **Cons:** digest becomes noisy and over-frequent; loses the "daily summary" value. Verdict: keep cron; let upstream events update the underlying data stores that digest reads from. |
| 2 | **Config reload detection** (SW-630) | `UserPromptSubmit` hook + mtime polling | **Keep as-is** | The hook already solves this cleanly with zero added infrastructure. Adding NATS `config.changed` would add a publisher (inotify → NATS) and subscriber (session reload) for a problem that is fully solved by a 10-line shell hook. **Pros of event-driven:** push-based, no per-prompt check. **Cons:** requires inotify daemon, NATS up at all times, more moving parts than a shell script. Verdict: the mtime hook is the right tool; don't over-engineer it. |
| 3 | **Project health check cron** (SW-539) | Not built | **Keep cron as trigger, add event output** | Health checks are time-triggered (daily), not reactive. Running them "on every push" via NATS events would be expensive and noisy for 13 projects. The cron trigger is correct. The only event-driven improvement is publishing `health.check.completed` so the digest can surface regressions immediately (already in §Candidates, Phase 5). |
| 4 | **MCP read operations** (ai-core MCP) | Request/response | **Keep request/response** | `query_tasks`, `get_priority_guidance`, etc. are request/response reads from SQLite. Pub/sub adds no value to reads — there's no "subscriber" for a query result. Event-driven applies to the *write side* (task.updated) not the read side. |
| 5 | **aido research queue** (aido) | Pull (manual dequeue) | **Keep pull for queue, add events for observability** | The research queue is a sequential pipeline with manual gates — converting it to event-driven would remove intentional human pacing. Only the *visibility* layer (aido.run.started/completed) should use events; queue management stays pull-based. |
| 6 | **Backlog staleness detection** (SW-13) | Cron | **Keep cron as primary, add event trigger as supplement** | Staleness is inherently time-based ("task not touched in 7 days"). A cron is the correct primary trigger. `task.updated` events can *reset* the staleness clock in real time, but can't replace the cron scan for tasks that are stale precisely *because* nothing happened. |

---

## Data Model

### NATSClient (existing — `messaging.py`)

```python
@dataclass
class NATSClient:
    servers: list[str]  # default: ["nats://localhost:4222"]
    nc: nats.NATS | None  # None until connected

    async def publish(subject: str, data: dict) -> bool
    async def subscribe(subject: str, callback: Callable) -> None  # blocking
    async def close() -> None
```text

### Event Envelope (convention, not enforced by schema)

```json
{
  "subject": "fleet.worker.sw-1.heartbeat",
  "machine": "server",
  "ts": 1742832000,
  "data": { ... subject-specific payload ... }
}
```text

### NATS Config Section (`~/.config/ai-cli/config.toml`)

```toml
[messaging]
nats_servers = ["nats://localhost:4222"]
```text

### Telemetry SQLite Schema (Phase 4, SW-16)

```sql
CREATE TABLE events (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,           -- Unix timestamp
    subject   TEXT NOT NULL,           -- NATS subject (mirrors pub/sub)
    machine   TEXT NOT NULL,
    session   TEXT,
    data      TEXT NOT NULL            -- JSON blob
);
CREATE INDEX idx_events_subject_ts ON events(subject, ts);
```text

---

## Integration

| System | Design Doc | Relationship |
|--------|-----------|--------------|
| Fleet Management | `docs/designs/fleet-management.md` | This doc extends the NATS Nervous System defined there. `fleet.worker.*` subjects are unchanged. |
| CC Sync Phase 2 | `docs/designs/cc-sync-phase2.md` | `sync.pull.requested` is the first cross-machine event trigger. `memory.dream.*` subjects extend the sync safety model (SW-644). |
| Shared Memory (SW-618) | `docs/research/shared-memory-cc-fleet.md` | NATS `memory.learning.stored` provides the push notification layer; SQLite + ai-core MCP is the store. |
| Fleet Management Plan | `docs/plans/fleet-management-plan.md` | Implementation sequencing for SW-501 aligns with Phase 2 here. |
| Telemetry Research | `docs/research/telemetry-event-design-early-stage-apps.md` | SQLite WAL + background writer pattern for telemetry write path (Phase 4). |

---

## Implementation Phases

### Phase 1 — Foundation ✅ Complete (2026-03-24)

**Goal:** Prove NATS infrastructure end-to-end with one real cross-machine use case.

- [x] `NATSClient` with bounded retry, `_publish()` guard, `subscribe()` (SW-614)
- [x] `ai sync push` publishes `sync.pull.requested`
- [x] `ai sync watch` subscribes and auto-pulls
- [x] Heartbeat JSON quoting fix in engine script
- [x] CLI error handling for `publish-event` / `publish-heartbeat`

### Phase 2 — Session Lifecycle + Fleet Wiring ✅ Complete (2026-03-25)

**Goal:** Fleet dashboard (SW-501) has live data; session start/stop events flow.

- [x] NATSClient upgraded to JetStream: `js.publish()` for durable subjects, stream auto-creation, fallback to core NATS
- [x] `subscribe_durable()` for JetStream durable consumers with ACK
- [x] Wire `fleet.worker.{id}.heartbeat` from engine script to NATS (verified end-to-end via `ai internal publish-heartbeat`)
- [x] Add `session.{id}.started` publish to engine script session loop via `ai internal publish-session-event`
- [x] Add `session.{id}.stopped` publish to engine script session loop via `ai internal publish-session-event`
- [x] `ai sync watch` auto-starts in engine session loop with PID file guard + JetStream durable consumer
- [ ] Fleet dashboard subscriber (SW-501) reads from `fleet.worker.*` — deferred to SW-501

**Gate:** Human review — verify heartbeat + session events appear in fleet dashboard before Phase 3.

### Phase 3 — Memory / Sync Safety ✅ Complete (2026-03-25)

**Goal:** `ai sync push` is safe around auto-dream; cross-session memory notifications work.

- [x] SW-644: `ai memory watch` daemon — inotify watcher (via watchdog) on `~/.claude/projects/*/memory/MEMORY.md`; publishes `memory.dream.started` on write event, `memory.dream.completed` with 2s debounce after last write. Auto-starts alongside `ai sync watch` in session loop. Linux/Hetzner-only; Mac follow-up tracked as SW-655.
- [x] Guard in `ai sync push`: checks for active dream (recent MEMORY.md mtime + memory-watch PID), waits up to 30s for `memory.dream.completed`, proceeds regardless. Non-fatal if NATS unavailable.
- [ ] SW-618: `memory.learning.stored` — blocked: `store_learning` MCP tool does not exist yet

**Gate:** Human review — sync push safety verified with simulated dream scenario.

### Phase 4 — Telemetry & Quota ✅ Complete (2026-03-25)

**Goal:** Claude usage is tracked and alerted; user behavior telemetry collects data.

- [x] SW-613: `ai quota watch` daemon polls Claude usage file/CLI; publishes `quota.threshold.{50,75,90}` with per-day deduplication; fires OS notifications via `notify-send`
- [x] Notification manager integrated into quota watcher (notify-send on Linux)
- [x] SW-16: `record_event()` writes to SQLite WAL + publishes `telemetry.action.*` to JetStream; `ai telemetry writer` background consumer
- [x] Telemetry consent gate — opt-out via `[telemetry] enabled = false` in config.toml (default: enabled)

**Gate:** Human review — quota alert fires at 50% threshold in dry-run before live.

### Phase 5 — Cross-Project / aido / Full Fan-Out ✅ Complete (2026-03-25)

**Goal:** All polling-reducible patterns replaced; platform-wide observability.

- [x] aido publishes `aido.run.started/completed` with cost summary via `ai internal publish` subprocess (SW-647)
- [x] `task.updated` event from ai-core MCP write tools (update_task, toggle_task, add_task_relationship) → immediate curation trigger (additive to cron)
- [x] `health.check.completed` published by `save_health_run()` after each health check with pass/fail summary
- [x] `store_learning` MCP tool publishes `memory.learning.stored` to NATS (SW-618)
- [ ] SW-609/610/611: mobile push, teleportation protocol, fleet web UI — all downstream of Phase 2 events (P3)

**Gate:** Human review after aido bridge lands.

> **Feedback Round 1:** Does the phasing feel right — too big, too small? Should anything move earlier or later?
> - its fine. I'm going to have you do all 5 phases right away anyway tbh

> **AI Response Round 1:**
> Noted — all 5 phases will be implemented in one batch. The phase labels are kept as documentation of sequencing and dependencies, but the human review gates are checkpoints rather than true blockers. Phase 1 is already complete. Implementation starts at Phase 2.

---

> **Feedback Round 2:** Any changes to phasing or sequencing after implementation begins?
> - Phase 3 inotify watcher (Option B) approved. Incorporate into Phase 3 directly — no mtime pre-check intermediate step. inotify is Linux-only (Hetzner). Mac follow-up task added as SW-655.

> **AI Response Round 2:**
> Phase 3 updated: `ai memory watch` inotify daemon is the Phase 3 implementation directly (no mtime pre-check step). SW-655 follow-up task created for Mac watchdog/FSEvents support. Risk 2 updated to reflect the daemon approach resolves the "no CC hook" constraint.

---

> **Feedback Round 3:**
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

---

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | NATS not running on machine | All event-driven features silently no-op | `NATSClient` already returns `False` on publish failure without crashing. `ai sync watch` exits 1 and logs. Sync still works manually. Provide `ai nats status` health check command. |
| 2 | auto-dream hook not available (Anthropic API) | No CC-native event for dream start/stop | Resolved: inotify watcher daemon (`ai memory watch`) watches `MEMORY.md` directly — no CC hook needed. Mac sessions tracked as SW-655 (watchdog/FSEvents). |
| 3 | Cross-machine NATS delivery gap | Server doesn't receive events published from Mac | By design — staging repo is the cross-machine transport. `ai sync watch` on server triggers pull after receiving the event via the git mechanism. No NATS message crosses machine boundary. |
| 4 | Message loss | Heartbeat or event missed | Heartbeats: acceptable (next arrives in 30s). JetStream durable consumers + ACKs used for all non-ephemeral subjects from Phase 2 onward. Ephemeral subjects (heartbeats) use fire-and-forget on JetStream without ACKs. |
| 5 | Subject proliferation / naming drift | Subjects become inconsistent, hard to subscribe with wildcards | Canonical subject registry in this doc (§Subject Hierarchy). All new subjects must be added here before implementation. |

---

## Open Questions

1. **auto-dream hook availability**: Can we hook into Anthropic's auto-dream to publish `memory.dream.started/completed`? Or do we need to poll for the dream lock file? What does the dream write process look like on disk?

2. **`ai sync watch` startup**: Should `ai sync watch` be started automatically by the CC Start hook, or manually in a tmux pane? Auto-start is more reliable but risks multiple subscribers if the hook fires multiple times.

3. ~~**Cross-machine NATS (future)**~~ **Resolved**: Scoped to Hetzner-only for all Phase 2–5 work. Follow-up task `[SW-913]` tracks evaluation of shared NATS cluster / Tailscale tunnel when staging repo becomes a bottleneck or P3 features require real-time cross-machine delivery.

4. **Telemetry consent model**: What's the right default — opt-in or opt-out? The platform is personal (single user), so consent is less critical, but the pattern matters if this becomes multi-user.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. auto-dream hook:
>
>    - you tell me? what can we do here? what do you recommend? investigate as necessary and then give me options, pros/cons, and a recommendation and explain your reasoning
> 2. sync watch startup:
>
>    - definitely auto-start. how can we mitigate the risks?
> 3. cross-machine NATS:
>
>    - we'll start by prioritizing just working on dev server. I don't expect to do any dev on local machine cc sessions unless it's required to fix something on dev server tbh. we can worry about cross-machine NATS (multiple servers and/or local machine) as a separate follow-up task. go ahead and create that task and link it to whatever task we are using for this work
> 4. telemetry consent:
>
>    - opt-out

> **AI Response Round 1:**
>
> **OQ1 — auto-dream hook (investigated):**
> Findings: CC auto-dream writes only `MEMORY.md` per project. It creates `~/.claude/projects/{hash}/memory/.consolidate-lock` — but this is a "last ran" timestamp, NOT an in-progress lock. There is no "dream active right now" signal from CC. Three options:
>
> | # | Option | Pros | Cons |
> |---|--------|------|------|
> | A | **mtime pre-check**: before sync push, check if `MEMORY.md` was modified in last 60s; if so, sleep + retry once | Zero new infrastructure; ~99% reliable in practice | Not a true lock; window is approximate |
> | B | **inotify watcher daemon**: background process watches `MEMORY.md` for writes, publishes `memory.dream.started/completed` on NATS | Proper event-driven; enables the planned NATS subjects | Another daemon to manage; Linux-only (inotify); Hetzner-only for now |
> | C | **Accept the race**: git commits are atomic at the file level; risk window is tiny | No infrastructure | Not zero risk; partial MEMORY.md could theoretically land in a commit |
>
> **Recommendation: Option A short-term (Phase 3), Option B medium-term.** The mtime check is simple and good enough to ship Phase 3. Option B becomes the proper solution once the session loop is running daemons anyway (sync watch is already one). Since dev is Hetzner-only, inotify is available. The NATS subjects (`memory.dream.started/completed`) stay in the subject hierarchy as planned — they just get published by the inotify watcher rather than a CC hook.
>
> **OQ2 — sync watch auto-start mitigations:**
> Two-layer guard: (1) **PID file** — `ai sync watch` writes its PID to `~/.ai-cli/sync-watch.pid` on start; checks if that PID is alive before starting, exits if so. (2) **JetStream durable consumer** — since we're using JetStream, `ai sync watch` registers as a named durable consumer (`sync-watch`). JetStream delivers each message to exactly one consumer in the group even if multiple connections exist. Belt and suspenders: PID file prevents duplicate processes; durable consumer ensures idempotent delivery if a duplicate slips through.
>
> **OQ3 — cross-machine NATS:**
> Scoped to dev server (Hetzner) only. Follow-up task added to roadmap. All Phase 2–5 implementation targets Hetzner. Cross-machine NATS (Tailscale tunnel or shared cluster) is a separate future initiative.
>
> **OQ4 — telemetry consent:**
> Opt-out confirmed. Default on; disable via `[telemetry] enabled = false` in config.toml.

---

> **Feedback Round 2:** Any changes to the open question resolutions?
> 1. auto-dream: Option B (inotify watcher daemon) approved. inotify is Linux-only — noted. Add follow-up task for Mac (watchdog). Incorporate into Phase 3 now.
> 2. sync watch auto-start mitigations: approved.
> - All open questions resolved. Doc approved. Implement phases 1–5 now. Human gate at end for review.

> **AI Response Round 2:**
> All resolutions confirmed. SW-655 created for Mac watchdog/FSEvents support (linked to SW-913). Phase 3 updated to implement inotify daemon directly. Doc status set to APPROVED. Implementation of Phases 2–5 begins now.

---

> **Feedback Round 3:**
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

---

## Approval Log

| Date | Round | Decisions / Approvals |
|------|-------|----------------------|
| 2026-03-25 | Round 1 | Decisions 1–5 approved. JetStream from day 1 confirmed. Cross-machine scoped to Hetzner-only; SW-913 follow-up created. auto-dream: mtime pre-check short-term, inotify watcher medium-term. Sync watch: auto-start with PID file + JetStream durable consumer guard. Telemetry consent: opt-out. All 5 phases in one batch. |
| 2026-03-25 | Round 2 | **APPROVED.** Decision 4 revised: JetStream removed as storage option (it's the transport); SQLite WAL vs external pipeline only. Phase 3 updated: inotify daemon (`ai memory watch`) is the direct implementation — no mtime pre-check step. SW-655 created for Mac watchdog/FSEvents follow-up. All open questions resolved. Implementation begins. |
