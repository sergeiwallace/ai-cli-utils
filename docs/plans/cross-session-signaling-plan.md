# AI-CLI-14: Cross-Session Signaling — Implementation Plan

**Status:** DRAFT
**Created:** 2026-04-01
**Task:** AI-CLI-14

## Table of Contents

- [Overview](#overview)
- [Options](#options)
- [Recommendation](#recommendation)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Cross-session signaling ("data migration done, start ART-14") currently uses `tmux send-keys`, which is unreliable when a CC session is mid-generation — keystrokes land nowhere or corrupt the prompt. The fix is to layer NATS pub/sub on top of the existing file-based handoff queue: NATS delivers signals in real time, the file queue retains durability. Mac sessions need an auto SSH tunnel since NATS is Hetzner-local only.

## Options

### Option A: NATS-native (replace file queue entirely)

All handoff state lives in NATS JetStream. Files eliminated.

**Pros:**
- Single source of truth
- Real-time delivery natively

**Cons:**
- NATS down = handoff queue gone entirely (no durability)
- Mac sessions lose handoffs when tunnel is not up
- Bigger migration surface — current file queue is simple and works

### Option B: Dual-layer (NATS push + file queue persistence)

NATS delivers the signal in real time; file queue remains the durable record. Both always written on `post_handoff`. Subscriber prints a visible banner on receipt.

**Pros:**
- Graceful degradation: NATS down → file queue still works, session picks up at next `ai handoff check`
- No existing behavior changes — purely additive
- Mac access via auto SSH tunnel, transparent to callers
- Matches existing codebase pattern (NATS always non-fatal)

**Cons:**
- Slightly more code than Option A (two write paths)
- SSH tunnel adds complexity on Mac

### Option C: File queue + precmd polling watcher (no NATS)

A bash `precmd` hook polls the file queue every N seconds and prints a banner when a new item appears.

**Pros:**
- No NATS dependency at all
- Trivially portable (Mac, Hetzner, anywhere)

**Cons:**
- Polling latency (5-30s delay)
- Adds shell hook complexity
- Doesn't leverage NATS investment already in the codebase

### Recommendation

**Option B.** The file queue stays as the durable safety net — nothing regresses. NATS is layered on top purely as a push notification channel, consistent with how every other NATS integration in the codebase works (non-fatal, best-effort). The auto SSH tunnel is self-contained in `NATSClient.connect()` and transparent to all callers. Option A is fragile (NATS is a single point of failure for the queue). Option C adds polling complexity and ignores the existing NATS infrastructure.

## Task Breakdown

### T-01: Add handoff stream to STREAM_CONFIG

**Size:** S
**Batch:** 1

Add `"handoff": ["handoff.>"]` to `STREAM_CONFIG` in `messaging.py`. JetStream auto-creates the stream on first publish (existing pattern via `_ensure_stream`).

**Deliverables:**
- `src/ai_cli/messaging.py`

**Acceptance criteria:**
- [ ] `STREAM_CONFIG` contains `"handoff": ["handoff.>"]`
- [ ] Existing stream entries unchanged

**Dependencies:** None

---

### T-02: Wire NATS publish into post_handoff()

**Size:** S
**Batch:** 1

After writing the file in `post_handoff()`, publish to subject `"handoff.{project}"` with payload: `{id, title, project, priority, message, created_by, ts}`. Non-fatal if NATS unavailable — same try/except pattern used throughout.

**Deliverables:**
- `src/ai_cli/main.py`

**Acceptance criteria:**
- [ ] `post_handoff()` publishes to `"handoff.{project}"` after file write
- [ ] Publish failure does not raise or exit — silent continue
- [ ] File is still written regardless of NATS state

**Dependencies:** T-01

---

### T-03: Auto SSH tunnel in NATSClient.connect() for Mac

**Size:** M
**Batch:** 1

In `NATSClient.connect()`, before attempting connection:
- If `HUMANWARE_HOST == "mac"` and port 4222 is not reachable locally (1s socket timeout): open tunnel via `subprocess.Popen(["ssh", "-fNL", "4222:localhost:4222", "sergei@178.104.70.139"])`
- Wait up to 3s for port to become available, then proceed with normal connect
- Store tunnel PID for cleanup on disconnect

**Deliverables:**
- `src/ai_cli/messaging.py`

**Acceptance criteria:**
- [ ] When `HUMANWARE_HOST=mac` and port 4222 unreachable, SSH tunnel is opened
- [ ] Connect proceeds after tunnel is up (port reachable within 3s)
- [ ] When port already reachable, no tunnel is opened
- [ ] Non-`mac` hosts: tunnel logic skipped entirely
- [ ] Tunnel PID stored and closed on `NATSClient.close()`

**Dependencies:** None (independent of T-01/T-02)

---

### T-04: signal-watch internal subcommand + banner output

**Size:** M
**Batch:** 2

Add `ai internal signal-watch <project> <session_id>` subcommand. Subscribes to `"handoff.{project}"` via `subscribe_durable` with consumer name `"{session_id}-signal-watcher"`. On message receipt: prints a visible banner to stdout (the CC terminal). Non-fatal if NATS unavailable — exits cleanly with no output.

Banner format:
```
╔══════════════════════════════════════╗
║  📬 HANDOFF: <title> [P<priority>]  ║
║  from: <created_by>                  ║
╚══════════════════════════════════════╝
```

**Deliverables:**
- `src/ai_cli/main.py` (new internal subcommand handler)

**Acceptance criteria:**
- [ ] `ai internal signal-watch <project> <session_id>` subscribes durably to `handoff.<project>`
- [ ] Banner printed to stdout on message receipt
- [ ] Exits cleanly (no error/traceback) if NATS unavailable
- [ ] Durable consumer name is `"{session_id}-signal-watcher"`

**Dependencies:** T-01

---

### T-05: Launch signal-watch in bash template watcher

**Size:** S
**Batch:** 2

In `get_engine_script()`, alongside the existing `start_watcher()` background process, launch `ai internal signal-watch "$project_prefix" "$tmux_session"` as an additional background process. Store PID and kill on EXIT trap (alongside `watcher_pid`).

**Deliverables:**
- `src/ai_cli/main.py` (bash template in `get_engine_script`)

**Acceptance criteria:**
- [ ] `ai internal signal-watch` launched as background process at session start
- [ ] PID killed in EXIT trap
- [ ] If `ai internal signal-watch` exits (NATS unavailable), session continues normally

**Dependencies:** T-04

---

### T-06: ai handoff post --remote flag

**Size:** S
**Batch:** 2

Add `--remote` flag to `ai handoff post`. When set, SSHes to Hetzner and runs `ai handoff post` there directly. Useful as fallback when NATS tunnel is down on Mac or posting to a remote project.

Usage: `ai handoff post --remote <project> "<title>" <priority> "<message>"`

**Deliverables:**
- `src/ai_cli/main.py`

**Acceptance criteria:**
- [ ] `--remote` flag SSHes to `sergei@178.104.70.139` and runs `ai handoff post` with same args
- [ ] Without `--remote`, existing behavior unchanged
- [ ] SSH failure exits with non-zero and prints error

**Dependencies:** None

---

### T-07: Tests

**Size:** L
**Batch:** 2

**Deliverables:**
- `tests/test_main.py` (handoff NATS publish, signal-watch, --remote flag)
- `tests/test_messaging.py` (SSH tunnel logic)

**Acceptance criteria:**
- [ ] `post_handoff()` test: mock `NATSClient`, verify `publish` called with correct subject + payload
- [ ] `post_handoff()` test: NATS failure → file still written, no exception raised
- [ ] `signal-watch` test: mock subscribe, verify banner printed on message receipt
- [ ] `signal-watch` test: NATS unavailable → exits cleanly, no output
- [ ] SSH tunnel test: mock `subprocess.Popen` + socket, verify tunnel opened when port unreachable on Mac
- [ ] SSH tunnel test: port already reachable → no tunnel opened
- [ ] `--remote` test: verify SSH exec called with correct args
- [ ] All existing handoff tests still pass
- [ ] 100% line coverage maintained

**Dependencies:** T-01–T-06

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03 | NATS stream + publish + Mac tunnel | Human review optional (no user-visible change) |
| 2 | T-04, T-05, T-06, T-07 | Signal-watch, bash template, --remote, tests | UAT + human approval before ship |

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Now | Approve scope, approach, Option B |
| UAT | After Batch 2 | Approve before pushing to main |

## Open Questions

1. Should the banner use emoji (📬) or plain ASCII only for maximum terminal compatibility?
> -

2. Should `signal-watch` also consume *existing* durable messages on startup (i.e. replay missed signals), or only receive new ones?
> -

---

> **Feedback Round 1:**
> - Scope / task breakdown:
>
>    -
> - Batching:
>
>    -
> - Options:
>
>    -

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
