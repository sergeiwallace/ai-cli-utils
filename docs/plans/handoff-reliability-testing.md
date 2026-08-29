---
title: AI-CLI-16 — Handoff Reliability Testing
category: plan
tags: [handoff, reliability, testing, ai-cli-16]
status: in_progress
source: session-2026-04-06
---

# AI-CLI-16 — Handoff Reliability Testing

> **Superseded:** In v0.8.0 the handoff command, queue, NATS ingress, and
> automatic delivery hooks were retired. The scenarios below describe the
> removed system and are retained only as historical test evidence.

**Task:** AI-CLI-16
**Status:** In progress — testing scenario by scenario

## Table of Contents

- [System Overview](#system-overview)
- [Implementation Status](#implementation-status)
- [Test Scenarios](#test-scenarios)
- [Scenario 1: Same-Machine, Session Idle at Prompt](#scenario-1-same-machine-session-idle-at-prompt)
- [Scenario 2: Same-Machine, CC Mid-Generation](#scenario-2-same-machine-cc-mid-generation)
- [Scenario 3: Same-Machine, Session Not Yet Started](#scenario-3-same-machine-session-not-yet-started)
- [Scenario 4: Cross-Machine Mac → Hetzner](#scenario-4-cross-machine-mac--hetzner)
- [Scenario 5: Cross-Machine Hetzner → Mac](#scenario-5-cross-machine-hetzner--mac)
- [Bugs Found](#bugs-found)
- [Approval Log](#approval-log)

---

## System Overview

The handoff system delivers work between CC sessions automatically via 5 pickup layers:

| Layer | Mechanism | State Covered | Implemented |
|:---|:---|:---|:---|
| L1 | CC Stop hook | Mid-generation — picks up after turn | ❌ Not implemented |
| L2 | UserPromptSubmit hook | User typing — blocks, prepends task | ❌ Not implemented |
| L3 | send-keys nudge | Idle at prompt | ❌ Explicitly skipped (unreliable on Linux) |
| L4 | While-loop pickup | CC just exited, between runs | ✅ Implemented |
| L5 | Startup drain (sync) | Session just launching | ✅ Implemented |
| SW | Signal-watch NATS realtime | Session running, via NATS delivery | ✅ Implemented (B-01 fixed) |

**Net result:** Only L4, L5, and signal-watch are active. No mechanism covers the "CC is running mid-task" state (L1 was never implemented). The system relies on the next natural CC exit to flush the pending file.

### File locations

| File | Purpose |
|:---|:---|
| `~/.claude/handoff-queue/pending/*.md` | Durable queue |
| `~/.local/state/ai-cli/handoff-pending-{session}` | Per-session pickup trigger |
| `~/.local/state/ai-cli/cc-resume-prompt-{session}` | Prompt injected on CC restart |
| `~/.local/state/ai-cli/handoff-events.jsonl` | Observability log |

### Key invariants

- `for_machine` field in handoff file must match `AI_HOST` env var on receiving machine, or signal-watch silently drops it
- `post_handoff` requires `--for-machine` — no implicit default
- Signal-watch subscribes to `handoff.{project_name}` (directory name, e.g. `handoff.ai-cli-utils`)
- `post_handoff` publishes to same subject — B-02 confirmed fixed

---

## Implementation Status

### Fixed bugs (confirmed by code review)

- **B-01:** `subscribe_durable` didn't block → fixed, now has `while True: await asyncio.sleep(1)`
- **B-02:** signal-watch used `task_prefix` instead of `project_name` for NATS subject → fixed, now uses directory name

### Never implemented (from original plan)

- **L1** (Stop hook): not in bash template, no `hooks.Stop` registration
- **L2** (UserPromptSubmit hook): not in bash template
- **L3** (send-keys nudge): explicitly skipped — code comment: "pane_current_command is always bash on Linux (CC is a child of bash), so nudging is unreliable"

---

## Test Scenarios

Each scenario is tested manually, results recorded below.

**Setup for each test:**
- Session under test: a running `ai c` session in a project directory
- Poster: another session (or shell) running `ai handoff post`
- Verify: check `handoff-events.jsonl` for event trail, check that CC actually picks up task

**Test command template:**
```bash
ai handoff post --for-machine <hetzner|mac> \
  "Test handoff $(date +%s)" P1 <project-name> \
  "Task body: verify pickup works"
```text

Note: `ai handoff post` takes positional args (`<title> <priority> <project> <message>`), not named flags.

---

## Scenario 1: Same-Machine, Session Idle at Prompt

**Setup:** CC session is running, at idle `❯` prompt. Post a handoff targeting same machine.

**Expected pickup path:** Signal-watch (NATS realtime delivery) → writes `handoff-pending-{session}` → on next CC exit (user triggers it), while-loop (L4) reads the pending file and restarts CC with `--continue`.

**Gap:** No mechanism wakes CC while it's idle. Session won't pick up until user exits CC or it exits naturally.

| Run | Date | Result | Layer | Notes |
|:----|:-----|:-------|:------|:------|
| 1 | 2026-04-11 | ✅ Pass | SW (NATS realtime) | Direct `ai internal signal-watch` confirmed; Circus-managed path was broken (B-04, now fixed) |

---

## Scenario 2: Same-Machine, CC Mid-Generation

**Setup:** CC session is actively running a task (mid-agentic-loop). Post a handoff.

**Expected pickup path:** Signal-watch claims handoff → writes `handoff-pending-{session}` → when CC finishes current task and exits, L4 reads it.

**Gap:** L1 (Stop hook) is not implemented — so if CC finishes its task and stays at the prompt (user doesn't exit), the pending file just sits. L4 only fires after CC *exits*, not after it finishes a turn.

| Run | Date | Result | Layer | Notes |
|:----|:-----|:-------|:------|:------|
| 1 | 2026-04-11 | ✅ Pass | SW (NATS realtime) | Same SW delivery path as Scenario 1; pending file is written immediately, L4 picks up on next exit |

---

## Scenario 3: Same-Machine, Session Not Yet Started

**Setup:** No CC session running yet. Post a handoff, then launch `ai c`.

**Expected pickup path:** L5 (`handoff-drain` runs before first CC launch) → finds pending file → writes `cc-resume-prompt-{session}` → CC launches with `--continue` on the task.

**Note:** This is the most reliable path. The drain runs synchronously before CC starts.

| Run | Date | Result | Layer | Notes |
|:----|:-----|:-------|:------|:------|
| 1 | 2026-04-11 | ✅ Pass | L5 (startup drain) | Local file scan claimed handoff, wrote `cc-resume-prompt-{session}` correctly; events logged |

---

## Scenario 4: Cross-Machine Mac → Hetzner

**Setup:** Mac session posts `--for-machine hetzner`. Hetzner session is running.

**Expected path:** Mac `ai handoff post` → writes file to Mac's handoff queue → publishes to NATS via auto SSH tunnel → Hetzner signal-watch receives, checks `for_machine == "hetzner"` (matches `AI_HOST=hetzner`) → claims file, writes `handoff-pending-{session}` → L4 picks up on next CC exit.

**Risk:** The handoff file is also written locally on Mac (to Mac's queue) — Hetzner's startup drain won't find it there. Reliability depends entirely on NATS delivery succeeding, AND Hetzner's `AI_HOST` being exactly `hetzner`.

| Run | Date | Result | Layer | Notes |
|:----|:-----|:-------|:------|:------|
| — | — | — | — | Pending live test with Hetzner session running |

---

## Scenario 5: Cross-Machine Hetzner → Mac

**Setup:** Hetzner session posts `--for-machine mac`. Mac session is running.

**Expected path:** Same as Scenario 4 but reversed. Mac's signal-watch must be connected to NATS via its SSH tunnel. Mac must have `AI_HOST=mac`.

**Risk:** Mac SSH tunnel must be up. Mac's signal-watch must already be running (started when `ai c` launched). If Mac session is in a different project than the handoff's project name, NATS subject won't match.

| Run | Date | Result | Layer | Notes |
|:----|:-----|:-------|:------|:------|
| — | — | — | — | Pending live test with cross-machine session running |

---

## Bugs Found

| ID | Scenario | Description | Status |
|:---|:---------|:------------|:-------|
| B-01 | All | `subscribe_durable` didn't block — signal-watch exited immediately | Fixed (code review confirmed) |
| B-02 | All | signal-watch subscribed to wrong NATS subject (task_prefix vs project_name) | Fixed (code review confirmed) |
| B-03 | 1, 2 | No wakeup mechanism when CC is idle or mid-task — L1/L2/L3 never implemented | Open — design decision needed |
| B-04 | 1, 2 | `autostart` is not a valid Circus `add` option — silently failed watcher registration via Circus | Fixed 2026-04-11 — removed from options dict |

---

## Approval Log

| Date | Decision | Notes |
|:-----|:---------|:------|
| 2026-04-06 | Start scenario-by-scenario testing | Begin with Scenario 3 (most reliable path) |
| 2026-04-11 | Scenarios 1/2/3 tested (same-machine) | L5 drain + SW NATS delivery both pass; B-04 found and fixed |
