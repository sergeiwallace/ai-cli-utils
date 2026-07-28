# AI-CLI-14: Cross-Session Signaling — Implementation Plan

**Status:** COMPLETE
**Created:** 2026-04-01
**Task:** AI-CLI-14

<!-- AIDO-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
  with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so
  they navigate in-window (incl. VS Code Remote-SSH). `aido toc check` validates this
  once AIDO-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful
  `###`" — a deterministic OR-rule: include a `###` when it (1) has child `####`,
  (2) its section body ≥ ~8-10 lines, (3) its parent `##` is allowlisted (Decisions /
  Open Questions / appendices), or (4) matches a pattern (`### Decision N`, `### D\d+`);
  `<!-- toc:skip -->` / `<!-- toc:include -->` on a heading override the heuristic. -->

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

### Layered approach (5 layers)

Auto-pickup uses layered mechanisms so every CC session state is covered. No single mechanism handles all states — they complement each other with graceful fallback.

**CC session states and which layer handles each:**

| State | Description | Primary layer | Fallback |
|-------|-------------|---------------|----------|
| **Working** | CC mid-agentic-loop | Stop hook (fires after turn) | While-loop (on CC exit) |
| **Idle (no typing)** | CC at prompt, user away | tmux send-keys nudge | UserPromptSubmit hook (on next interaction) |
| **User typing** | CC at prompt, user composing | UserPromptSubmit hook (blocks once, shows combined prompt) | Stop hook (after their turn) |
| **Between runs** | CC exited, bash while-loop active | While-loop reads pending file | — |
| **Session start** | CC hasn't started yet | Startup scan (already done) | — |

#### Layer 1: CC Stop hook (primary — Working, User Typing fallback)

Configured in `~/.claude/settings.json` per-session via the bash template (written on session start, removed on exit). Fires after every CC turn. Checks `handoff-pending-$session` marker file. If found: outputs `{"decision": "block", "reason": "<task content>"}` so CC continues with the queued task as its next message. If absent: exits 0, normal stop proceeds.

#### Layer 2: UserPromptSubmit hook (Idle → user returns, User Typing)

Same pattern as the existing config-reload hook. On prompt submission, checks for `handoff-pending-$session`. If found:
- Blocks the prompt (exit 2) with a message containing:
  1. The user's original prompt text
  2. Appended instructions to pick up the queued handoff task and remove it from queue
  3. The combined text is easy to copy-paste and resubmit
- Sets a "caught" flag so it only blocks **once**. If the user ignores the copy-paste and resubmits their original prompt, it goes through — the Stop hook will catch the pending task after that turn anyway.

#### Layer 3: tmux send-keys nudge (truly Idle, no user activity)

When signal-watch detects CC is running but the pane appears idle (via `tmux display-message` checking foreground command), it injects a message via `tmux send-keys` telling CC to check and pick up the queued handoff task. This is the only racy mechanism — if the user happens to be typing, Layers 1–2 catch it instead.

**Reliability tracking:** Log every send-keys attempt and outcome to `$_ai_state_dir/handoff-nudge-log.jsonl`. Track: timestamp, session, handoff ID, pane state detected, whether CC picked up the task within 60s. Any task pending >5min without pickup → warning logged. Any task pending >30min → P0 alert surfaced to user's session.

**iTerm2 compatibility:** send-keys behavior may differ under iTerm2's tmux integration. Flag for manual testing; rely on observability data to catch issues rather than upfront toy-example testing.

#### Layer 4: While-loop fallback (Between runs)

Already implemented. After CC exits, the bash while-loop checks `handoff_pending_file`, writes content to `prompt_file`, and next CC invocation picks it up via `--continue`.

#### Layer 5: Startup scan (Session start)

Already implemented. signal-watch scans pending queue before NATS subscribe, claims unclaimed files.

### Race condition (multiple sessions competing)

`mv` (rename) is atomic on Linux. `claim_handoff()` already performs this move. First session to succeed wins; others get ENOENT and skip silently.

### Observability

**Single event log:** `$_ai_state_dir/handoff-events.jsonl` — every mechanism that touches the handoff lifecycle logs here. This is the primary testing strategy — production data surfaces failures instead of upfront toy-example testing.

**Event types logged across all layers:**

| Event | Source | Fields |
|-------|--------|--------|
| `handoff.posted` | `post_handoff()` | handoff_id, project, title, priority, target_session (if known) |
| `handoff.claimed` | signal-watch / startup scan | handoff_id, session, layer (startup_scan / nats_realtime), ts |
| `handoff.nudge_sent` | signal-watch send-keys | handoff_id, session, pane_state_detected, ts |
| `handoff.stop_hook_fired` | Stop hook script | handoff_id, session, pending_file_found (bool), ts |
| `handoff.prompt_hook_caught` | UserPromptSubmit hook | handoff_id, session, user_prompt_length, ts |
| `handoff.while_loop_pickup` | Bash while-loop | handoff_id, session, cc_exit_elapsed, ts |
| `handoff.session_start` | `ai c` launch | session, pending_count, ts |
| `handoff.session_restart` | While-loop auto-restart | session, restart_reason, pending_task_exists (bool), ts |
| `handoff.completed` | `ai handoff complete` | handoff_id, session, time_to_complete (from posted to completed), ts |
| `handoff.stale_warning` | Periodic check | handoff_id, session, pending_duration_min, ts |

**Gap detection:** A periodic check in signal-watch (every 60s) computes:
- Time since handoff was claimed but not completed
- Time since handoff was posted but not claimed
- Time since last session restart where a pending task existed but wasn't picked up within 2 minutes
- Time since a nudge was sent but no `stop_hook_fired` or `prompt_hook_caught` followed within 2 minutes

**Alert thresholds and mechanism:**
- Pending >5 minutes without pickup → warning banner in target session terminal
- Pending >30 minutes without pickup → **P0 alert banner in ALL sw-\* sessions** via NATS `handoff.stale` event (signal-watch in every session subscribes and prints the banner)
- Session restart with pending task that isn't picked up within 2 minutes → warning banner in that session
- Nudge sent but no pickup within 2 minutes → warning banner (send-keys may have failed)

**Banner format (stale alert):**
```text
==========================================
⚠ STALE HANDOFF: #42 "Fix login regression"
  Pending 32 min — not picked up by c-sw-1

  To resolve: direct the target CC session to
  create a P0 task in its project roadmap with
  due date today, or manually run:
    ai handoff check
==========================================
```text

The alert fires repeatedly (every 5 min after the 30-min threshold) until the task is claimed or manually dismissed. Dial back aggressiveness once observability data confirms reliability.

**`ai handoff status` command:** On-demand report showing all recent events, pending tasks, pickup latencies, and any gap warnings. Reads from `handoff-events.jsonl`.

## Prerequisites

### Project registry as hard requirement

All CC sessions MUST be launched via `ai c` (not bare `claude`). `ai c` is the enforcement point for registry completeness and handoff routing reliability.

**On every `ai c` launch:**

1. **Load registry** — parse `platform.toml`
2. **Schema validation** — every `[[projects]]` entry must have `name` and `task_prefix`. Both must be unique (case-insensitive). Fail with error if violated.
3. **Completeness scan** — diff `~/projects/*/` directories against registered `name` fields
4. **Unregistered directory found** → interactive prompt:
   ```text
   Unregistered project: "menos" (~/projects/menos)
   Suggested task_prefix: MENOS
   Add to registry? [Y/n, or enter custom prefix]:
   ```text
   - User confirms → append entry to `platform.toml` with sensible defaults (type, active, etc.)
   - User declines → **exit to shell**. Hard requirement — no session launch with incomplete registry.
5. **Proceed** — registry is guaranteed complete and valid for this session

**Handoff routing uses project directory name (not task_prefix) as the unique key.** NATS subjects: `handoff.{project_name}` (e.g., `handoff.ai-cli-utils`). Task prefix is only for tmux session naming and task display IDs. The registry provides the bidirectional mapping.

**Single load point:** Replace the current 4+ separate TOML parse calls with a single `load_project_registry()` that validates once and caches. All callers use the cached result.

## Known Bugs (fix before T-05)

### B-01: `subscribe_durable` doesn't block

`NATSClient.subscribe_durable()` in `messaging.py` sets up a JetStream callback but returns immediately — unlike `subscribe()` which has a `while True: await asyncio.sleep(1)` blocking loop. signal-watch exits after startup scan; the NATS subscription dies with the process. Real-time handoff delivery via NATS has never worked.

**Fix:** Add the same blocking loop to `subscribe_durable`.

### B-02: signal-watch uses task_prefix instead of project name for NATS subject

signal-watch subscribes to `handoff.{project_prefix}` (e.g., `handoff.ai-cli`) but handoffs are posted with `project: ai-cli-utils`. Subject mismatch → signal-watch never receives handoffs.

**Fix:** Pass `project_name` (directory name) to signal-watch instead of `project_prefix`. Update bash template to pass both values. NATS subjects use `handoff.{project_name}`.

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes each task from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
  gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
  structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
  review against the ACs), never self-declared "done". -->

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
- If `AI_HOST == "mac"` and port 4222 is not reachable locally (1s socket timeout): open tunnel via `subprocess.Popen(["ssh", "-fNL", "4222:localhost:4222", "user@192.0.2.1"])`
- Wait up to 3s for port to become available, then proceed with normal connect
- Store tunnel PID for cleanup on disconnect

**Deliverables:**
- `src/ai_cli/messaging.py`

**Acceptance criteria:**
- [ ] When `AI_HOST=mac` and port 4222 unreachable, SSH tunnel is opened
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
```text
==========================================
  HANDOFF: <title> [P<priority>]
  from: <created_by>
==========================================
```text

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

### T-05: Layered auto-pickup hooks + signal-watch nudge fix

**Size:** L
**Batch:** 2

Three sub-tasks implementing the layered auto-pickup design:

**5a — CC Stop hook (Layer 1):** Write a per-session hook script to `$_ai_state_dir/stop-hook-$tmux_session.sh` at session start. Register it in `~/.claude/settings.json` under `hooks.Stop`. The script: checks for `handoff-pending-$session` marker → if found, reads content, removes marker, outputs `{"decision": "block", "reason": "<content>"}` → CC continues with the task. If absent: exits 0 silently. Remove hook entry and script on EXIT trap.

**5b — UserPromptSubmit hook (Layer 2):** Add a per-session hook script `$_ai_state_dir/handoff-prompt-hook-$tmux_session.sh` registered under `hooks.UserPromptSubmit`. On prompt submission: checks for `handoff-pending-$session`. If found AND not already caught (no caught-flag file): blocks prompt (exit 2), outputs message containing user's original prompt + appended instructions to pick up the queued task + how to remove it from queue. Sets caught-flag so next submission goes through normally. If caught-flag already set: clears it, exits 0. Remove hook entry, script, and caught-flag on EXIT trap.

**5c — tmux send-keys nudge fix (Layer 3):** Fix signal-watch `_on_handoff()` to send an actual actionable message via `tmux send-keys` when pane is idle (CC at prompt, not `claude` as foreground). The message tells CC to check and pick up the queued task. Log every nudge attempt to `$_ai_state_dir/handoff-events.jsonl` with timestamp, session, handoff ID, pane state, and outcome.

**5d — Observability:** Log all pickup events (claim, nudge, Stop hook fire, UserPromptSubmit catch) to `$_ai_state_dir/handoff-events.jsonl`. Startup scan or periodic check flags tasks pending >5min (warning) or >30min (P0 alert to user).

**Deliverables:**
- `src/ai_cli/main.py` (bash template for hook registration/cleanup, signal-watch nudge fix)
- Hook scripts (written dynamically at session start, not static files)

**Acceptance criteria:**
- [ ] Stop hook registered on session start, removed on exit
- [ ] Stop hook outputs `{"decision": "block", ...}` when pending file present
- [ ] Stop hook exits 0 silently when no pending file
- [ ] UserPromptSubmit hook blocks once with combined prompt + task instructions
- [ ] UserPromptSubmit hook allows second submission through (clears caught flag)
- [ ] signal-watch sends actionable message via send-keys for idle panes
- [ ] signal-watch does NOT send-keys when CC is busy (foreground = `claude`)
- [ ] All pickup events logged to handoff-events.jsonl
- [ ] Pending >5min logged as warning, >30min as P0
- [ ] Hook entries and scripts cleaned up in EXIT trap
- [ ] `ai internal signal-watch` launched as background process at session start (already done)
- [ ] signal-watch PID killed in EXIT trap (already done)

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
- [ ] `--remote` flag SSHes to `user@192.0.2.1` and runs `ai handoff post` with same args
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

### T-08 (follow-up, not in scope): Send-keys reliability hardening

**Size:** M
**Batch:** — (implement when observability data shows send-keys failures)

Analyze `handoff-events.jsonl` for send-keys nudge failures. Potential fixes: `capture-pane` pre-check for empty input line, iTerm2-specific send-keys flags, longer delay between pane state check and key injection. Driven by production data, not speculation.

**Dependencies:** T-05

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03 | NATS stream + publish + Mac tunnel | No gate (no user-visible change) |
| 2 | T-04, T-05, T-06, T-07 | signal-watch, layered hooks, bash template, --remote, tests | UAT + human approval before ship |

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
| 2026-04-01 | Auto-pickup: two-path approach | Original design — superseded by layered approach |
| 2026-04-01 | Auto-pickup: 5-layer approach | Stop hook (L1), UserPromptSubmit hook (L2), send-keys nudge (L3), while-loop (L4), startup scan (L5). Observability-driven testing over toy examples. |
| 2026-04-01 | Project registry hard requirement | Registry validation + completeness scan on every `ai c` launch. Unregistered project → interactive prompt → exit to shell if declined. Handoff routing uses project directory name, not task_prefix. |
| 2026-04-01 | Bugs B-01/B-02 identified | subscribe_durable doesn't block (signal-watch exits immediately); signal-watch uses wrong key for NATS subject. Both must be fixed before T-05. |
