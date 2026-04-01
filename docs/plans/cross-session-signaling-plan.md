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

Auto-pickup: when a handoff arrives, the target session claims and starts it automatically — immediately if idle, after finishing if mid-generation. Multiple competing sessions (e.g. four sw sessions) use atomic file rename to ensure exactly one claims each task.

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

NATS delivers the signal in real time; file queue remains the durable record. Both always written on `post_handoff`. signal-watch background process handles auto-pickup.

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

## Auto-Pickup Design

### Two-path approach

**Path 1 — Session idle when signal arrives:**
signal-watch receives NATS message → attempts atomic claim (`mv pending/ → claimed/`) → if claim succeeds, sends a single empty newline to the tmux session via `tmux send-keys "" Enter` to wake CC → CC Stop hook fires → hook reads the claimed file and outputs task content as next prompt → CC continues with the task.

**Path 2 — Session mid-generation when signal arrives:**
signal-watch receives NATS message → writes `handoff-pending-$session` marker file (does NOT touch the tmux session) → CC Stop hook fires when generation finishes → hook attempts atomic claim → if claim succeeds, outputs task content → CC continues with the task.

**Race condition (multiple sessions competing):**
`mv` (rename) is atomic on Linux. `claim_handoff()` already performs this move. First session to succeed wins; others get ENOENT and skip silently. The Stop hook retries once in case of a brief race window, then gives up cleanly.

**Fallback (T-08):** If the minimal send-keys nudge proves unreliable in practice, a session-restart approach (signal-watch kills the idle session; bash loop restarts CC with handoff as initial prompt) can replace Path 1. Tracked as a follow-up.

### CC Stop hook

Configured in `~/.claude/settings.json` per-session via the bash template (written on session start, removed on exit). Checks `handoff-pending-$session` marker file. Non-fatal if file absent — normal stop proceeds.

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

### T-04: signal-watch internal subcommand

**Size:** M
**Batch:** 2

Add `ai internal signal-watch <project> <session_id>` subcommand. Subscribes durably to `"handoff.{project}"` (consumer name: `"{session_id}-signal-watcher"`, replay missed messages on reconnect). On message receipt:

1. Print ASCII banner to terminal
2. Attempt atomic claim via `claim_handoff()`
3. If claim succeeds and session is idle (`tmux display-message -p '#{pane_current_command}'` returns `bash`/`zsh`): send `tmux send-keys -t $session "" Enter` to wake CC
4. If claim succeeds and session is busy (`claude` running): write `$_ai_state_dir/handoff-pending-$session` marker file — Stop hook will handle it when generation finishes
5. If claim fails (another session won): skip silently

Banner format (plain ASCII):
```
==========================================
  HANDOFF: <title> [P<priority>]
  from: <created_by>
==========================================
```

Non-fatal if NATS unavailable — exits cleanly with no output.

**Deliverables:**
- `src/ai_cli/main.py`

**Acceptance criteria:**
- [ ] Subscribes durably with replay to `handoff.<project>`
- [ ] Banner printed on message receipt
- [ ] Atomic claim attempted; skip silently if already claimed
- [ ] Idle session: single empty send-keys sent to wake CC
- [ ] Busy session: `handoff-pending-$session` marker written
- [ ] Exits cleanly if NATS unavailable
- [ ] Durable consumer name is `"{session_id}-signal-watcher"`

**Dependencies:** T-01

---

### T-05: Stop hook + launch signal-watch in bash template

**Size:** M
**Batch:** 2

Two changes to `get_engine_script()`:

**5a — Stop hook:** Write a per-session `~/.claude/settings.json` entry (or append to existing) on session start that registers a `Stop` hook. The hook script: checks for `handoff-pending-$session` marker → if found, reads the claimed file, removes the marker, outputs task content → CC continues with it as the next prompt. Remove hook entry on session EXIT.

**5b — Launch signal-watch:** Alongside `start_watcher()`, launch `ai internal signal-watch "$project_prefix" "$tmux_session"` as a background process. Store PID and kill on EXIT trap.

**Deliverables:**
- `src/ai_cli/main.py` (bash template)

**Acceptance criteria:**
- [ ] `ai internal signal-watch` launched as background process at session start
- [ ] Stop hook registered for session on start, removed on exit
- [ ] Stop hook reads marker file and outputs task content when present
- [ ] Stop hook exits silently (no output) when marker absent
- [ ] signal-watch PID killed in EXIT trap
- [ ] If signal-watch exits (NATS unavailable), session continues normally

**Dependencies:** T-04

---

### T-06: ai handoff post --remote flag

**Size:** S
**Batch:** 2

Add `--remote` flag to `ai handoff post`. When set, SSHes to Hetzner and runs `ai handoff post` there directly. Useful as fallback when NATS tunnel is down or posting to a remote project from Mac.

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
- `tests/test_main.py` (handoff NATS publish, signal-watch, Stop hook, --remote flag)
- `tests/test_messaging.py` (SSH tunnel logic)

**Acceptance criteria:**
- [ ] `post_handoff()`: mock NATSClient, verify publish called with correct subject + payload
- [ ] `post_handoff()`: NATS failure → file still written, no exception raised
- [ ] `signal-watch`: mock subscribe, verify banner printed on receipt
- [ ] `signal-watch`: idle session → send-keys called
- [ ] `signal-watch`: busy session → marker file written, send-keys not called
- [ ] `signal-watch`: claim fails (already claimed) → silent skip
- [ ] `signal-watch`: NATS unavailable → exits cleanly
- [ ] Stop hook script: marker present → task content output, marker removed
- [ ] Stop hook script: marker absent → no output
- [ ] SSH tunnel: mock subprocess + socket, verify tunnel opened when unreachable on Mac
- [ ] SSH tunnel: port already reachable → no tunnel opened
- [ ] `--remote`: verify SSH exec called with correct args
- [ ] All existing handoff tests still pass
- [ ] 100% line coverage maintained

**Dependencies:** T-01–T-06

---

### T-08 (follow-up, not in scope): Session-restart fallback for idle pickup

**Size:** M
**Batch:** — (implement only if T-04 Path 1 proves unreliable)

Replace the minimal send-keys nudge for idle sessions with a session-restart approach: signal-watch kills the idle CC session via the existing exit signal mechanism; the bash loop restarts CC with the handoff file content as the initial prompt. Eliminates any reliance on tmux send-keys for the idle path.

**Dependencies:** T-04, T-05

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03 | NATS stream + publish + Mac tunnel | No gate (no user-visible change) |
| 2 | T-04, T-05, T-06, T-07 | signal-watch, Stop hook, bash template, --remote, tests | UAT + human approval before ship |

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Now | Approve scope, approach, Option B |
| UAT | After Batch 2 | Approve before pushing to main |

## Open Questions

1. Should the banner use emoji or plain ASCII?
> **Decision:** plain ASCII

2. Should `signal-watch` replay missed signals on startup (durable consumer) or new-only?
> **Decision:** durable with replay — missed signals while subscriber was down are recovered on reconnect

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
| 2026-04-01 | OQ1: plain ASCII banner | emoji rendering unreliable in tmux |
| 2026-04-01 | OQ2: durable consumer with replay | reliability over simplicity |
| 2026-04-01 | Auto-pickup: two-path approach | Stop hook for mid-gen, minimal send-keys nudge for idle; T-08 as fallback if nudge unreliable |
