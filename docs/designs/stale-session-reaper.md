---
title: "Safe Out-of-Band Stale-Session Reaper — Design Document"
category: design
tags: [design, session-management, reliability]
status: draft
template_version: "design-1.0.0"
---

<!-- doc:region name="overview" kind="replaceable" -->

# Safe Out-of-Band Stale-Session Reaper — Design Document

**Status:** DRAFT

**Created:** 2026-08-27

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
- [Stale-Session Reaper](#stale-session-reaper)
  - [Safety invariant](#safety-invariant)
  - [Periodic execution and lifecycle](#periodic-execution-and-lifecycle)
  - [Candidate evaluation and reap protocol](#candidate-evaluation-and-reap-protocol)
  - [Heartbeat recording](#heartbeat-recording)
- [Data Model](#data-model)
  - [Configuration](#configuration)
  - [Heartbeat ledger](#heartbeat-ledger)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
  - [Phase 1: Fail-closed evaluator](#phase-1-fail-closed-evaluator)
  - [Phase 2: Circus lifecycle and rollout](#phase-2-circus-lifecycle-and-rollout)
- [Implementation Audit](#implementation-audit)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
  - [D-1: Heartbeat persistence](#d-1)
  - [D-2: Reap authorization](#d-2)
  - [D-3: Watcher lifecycle and cadence](#d-3)
  - [D-4: Rollout mode](#d-4)
  - [D-5: Staleness threshold](#d-5)
  - [D-6: Launch-time cleanup boundary](#d-6)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Executive Summary

This design restores eventual cleanup of genuinely dead managed tmux sessions without allowing one session launch to terminate another session. A single Circus-managed watcher evaluates sessions asynchronously and authorizes a reap only when every pane leader is confirmed ended and a locally recorded heartbeat is older than a configured threshold. Every incomplete, malformed, unavailable, or contradictory observation preserves the session. The watcher is not registered automatically by session-launch commands; an operator starts it independently, and its default mode records candidates without killing them. Launch-time `cleanup_stale_sessions()` remains bookkeeping-only.

## Problem Statement

Managed tmux session names can remain occupied after a wrapper or host crashes. A previous launch-time global sweep used a one-time pane-PID liveness observation and could terminate unrelated live sessions when that observation was wrong. The shipped emergency change correctly removed all tmux kill authority from launch-time cleanup, but leaves dead session-name slots unreclaimed.

## Design Overview

**Status:** stub — to be filled during/after implementation.

## Stale-Session Reaper

### Safety invariant

The reaper is the only new component authorized to invoke `tmux kill-session`. In `reap` mode, it may terminate only after both of these conditions hold for the same tmux session instance:

1. tmux returns a valid session ID and a positive leader PID for every pane, and the process probe confirms every PID has ended or is a zombie;
2. a readable, schema-valid local heartbeat record for that session name exists and its locally recorded timestamp is older than the configured threshold.

This is conjunctive, not a score. A live process with a stale heartbeat, an ended process with a fresh heartbeat, a missing/malformed/future-dated record, an invalid PID, an empty pane list, an exception, or an unreadable tmux/state response is ineligible. `observe` mode runs exactly this predicate but never kills.

### Periodic execution and lifecycle

The package will provide a session-reaper management command group with `start`, `stop`, `status`, and hidden/internal `run` commands, following the existing Circus watcher pattern. `start` idempotently registers one `stale-session-reaper` watcher with the existing local Circus daemon. `run` evaluates once, sleeps 60 seconds, and repeats; Circus restarts it on unexpected exit.

No session creation, resume, attach, `cleanup_stale_sessions()`, or `ai c`/`ai p`/`ai g`/`ai cx` path may call `start`, `run`, or the evaluator. The operator starts the watcher separately. A launch therefore cannot synchronously evaluate or terminate another session.

### Candidate evaluation and reap protocol

Each interval queries tmux for managed panes using a delimiter-safe format containing `#{session_id}`, `#{session_name}`, and `#{pane_pid}`. The evaluator groups by both name and session ID, reads one ledger record per candidate, and evaluates the invariant.

An eligible `observe` candidate is logged with its name, opaque tmux ID, heartbeat age, and reason, without a terminating command. For an eligible `reap` candidate, the worker immediately re-queries the captured exact session ID and repeats every gate. Only then does it call `tmux kill-session -t <session-id>`, never by name. The immutable ID prevents a manually removed and recreated name from becoming the kill target. Failed revalidation, changed ID, or any tmux error preserves the candidate.

After a successful kill, the worker may remove its ledger record only after tmux confirms that the captured ID no longer exists. It never removes a record after ambiguous status or failed termination. Records without a current candidate are ignored.

### Heartbeat recording

The generated session wrapper already invokes `ai internal publish-heartbeat` about every 30 seconds. The internal command will validate its JSON, atomically write the local ledger, and then retain its current best-effort messaging publication. Message delivery, retention, and consumers never authorize a local reap.

The writer makes the XDG state directory with `pathlib`, writes and flushes a complete temporary file in that directory, then atomically replaces the final file. A write error stays non-fatal to the wrapper but creates no usable record, so the evaluator preserves the session. Logs use reason codes such as `heartbeat_missing`, `heartbeat_invalid`, `heartbeat_not_stale`, `pid_invalid`, `process_unknown`, `process_live`, `tmux_read_failed`, `revalidation_failed`, and `mode_observe`; they contain no prompt or agent content.

> **Feedback Round 1:** Does this approach feel right? What's missing?
> - <enter feedback here>

## Data Model

### Configuration

```toml
[stale_session_reaper]
# "observe" evaluates and logs candidates but never ends a tmux session.
mode = "observe"
stale_after_seconds = 600
```

Absent configuration equals these values. Only `observe` and `reap` are valid modes. `stale_after_seconds` must be an integer greater than zero. A missing table, invalid type/value, or invalid threshold logs a configuration error and preserves every session; no CLI flag can enable one-off reaping.

### Heartbeat ledger

Records live beneath the existing XDG state home at `session-heartbeats/<encoded-session-name>.json`, where the encoding is reversible and filesystem-safe. The schema is:

```json
{"version": 1, "session_name": "c-session-1", "recorded_at": 1787860800}
```

`recorded_at` is an integer Unix timestamp written locally, not accepted from wrapper payload. The evaluator accepts a record only if it is a JSON object with this version, an exact session-name match, and a finite timestamp not later than its current clock. Every other shape fails closed.

## Integration

`session_script.py` continues its existing heartbeat call; the internal handler adds ledger persistence before its best-effort message publication. Existing event publication is unchanged. `process_manager.py` gains Circus registration/removal helpers beside existing watchers, using the existing XDG state directory and package-resolved executable. `main.py` dispatches the management and internal run commands.

`cleanup_stale_sessions()` remains unchanged in authority: it may list tmux names solely for orphaned background-spare cleanup and stale terminal-profile bookkeeping, but must not import/call the evaluator, start Circus, read heartbeat records, or invoke `tmux kill-session`. Conversely, the watcher must not call `cleanup_stale_sessions()`, background-spare cleanup, or terminal-profile cleanup. This prevents a scheduling overlap from granting launch-time code termination authority.

## Implementation Phases

### Phase 1: Fail-closed evaluator

- **Scope:** Implement local heartbeat persistence and a standalone evaluator, without any launch-path integration.
- **Deliverables:**
  - Files created: `src/ai_cli/stale_session_reaper.py`, `tests/test_stale_session_reaper.py`.
  - Files modified: `src/ai_cli/main.py`, `src/ai_cli/session_script.py`, `src/ai_cli/config.py`.
- **Tasks + acceptance criteria:**
  - **T-1.1 Ledger persistence**
    - [ ] When `ai internal publish-heartbeat` receives valid JSON, the system shall atomically persist a locally timestamped record matching the session argument before best-effort messaging publication.
    - [ ] If JSON is invalid or the record cannot be written, then the system shall create no usable record and shall not terminate a tmux session.
  - **T-1.2 Corroborated evaluator**
    - [ ] When every pane leader is confirmed ended or zombie and a valid heartbeat is older than the threshold, the system shall mark that exact session ID eligible.
    - [ ] When only pane liveness or only heartbeat staleness indicates death, the system shall preserve the session and issue no kill command.
    - [ ] If a PID, record, tmux response, process-probe response, or configuration value is missing, malformed, unreadable, future-dated, or raises an error, then the system shall preserve the candidate and log a reason.
  - **T-1.3 Revalidation**
    - [ ] When a candidate is eligible in `reap` mode, the system shall re-query its captured session ID and repeat both gates before `tmux kill-session -t <session-id>`.
    - [ ] If its ID disappears/changes, it gains an unconfirmed pane, or either gate fails during revalidation, then the system shall issue no kill command.
- **Exit gate:** Focused non-mocked ledger round trips and mocked tmux/process boundaries prove every criterion; `pytest tests/test_stale_session_reaper.py -q` and focused `ruff check` pass; fresh-context review confirms no launch path calls the evaluator.

### Phase 2: Circus lifecycle and rollout

- **Scope:** Add independently invoked Circus management, configuration validation, documentation, and non-interaction regressions.
- **Deliverables:**
  - Files modified: `src/ai_cli/process_manager.py`, `src/ai_cli/main.py`, `src/ai_cli/config.py`, `README.md`, and focused process-manager/CLI tests.
- **Tasks + acceptance criteria:**
  - **T-2.1 Independent lifecycle**
    - [ ] When an operator runs the documented start command, the system shall register exactly one Circus watcher running the entry point at a 60-second cadence.
    - [ ] When any launch command or `cleanup_stale_sessions()` runs, the system shall neither register the watcher nor execute the evaluator.
    - [ ] If Circus is unavailable or registration fails, then the start command shall report failure and shall not fall back to synchronous scanning or termination.
  - **T-2.2 Observe-first rollout**
    - [ ] Where mode is absent or `observe`, the system shall log a fully corroborated candidate and issue no kill command.
    - [ ] Where mode is explicitly `reap`, the system shall terminate only candidates passing initial and revalidation gates.
    - [ ] If mode or threshold is invalid, then the system shall preserve all sessions and log the configuration error.
  - **T-2.3 Authority regressions**
    - [ ] When launch-time cleanup is exercised against managed tmux listings, the system shall not invoke `tmux kill-session` or a reaper entry point.
    - [ ] When a candidate is process-live/heartbeat-stale or process-ended/heartbeat-fresh, the system shall issue zero kill commands in both cases.
- **Exit gate:** Focused reaper, session, process-manager, and CLI tests pass; `ruff check src/ai_cli tests` and `ruff format --check src/ai_cli tests` pass; fresh-context diff review confirms only the worker owns new tmux kill authority.

> **Feedback Round 1:** Does the phasing feel right — too big, too small? Should anything move earlier or later?
> - <enter feedback here>

## Implementation Audit

| # | Section / Decision | Verified | Notes |
|---|--------------------|---------|-------|
| 1 | Design Overview filled with shipped behavior | - [ ] | |
| 2 | D-1 ledger is atomic, local, schema-validated, and delivery-independent | - [ ] | |
| 3 | D-2 requires both gates, revalidates, and targets session ID | - [ ] | |
| 4 | D-3 has no launch-path registration or execution | - [ ] | |
| 5 | D-4/D-5 default and invalid configuration fail closed | - [ ] | |
| 6 | D-6 preserves launch-time bookkeeping authority | - [ ] | |

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | The exact liveness false-positive trigger is unconfirmed; wrapper self-reload is only correlated. | A process reading could be transiently wrong. | Independent stale ledger gate, immediate revalidation, immutable ID target, and observe-first rollout. |
| 2 | Publisher or local storage fails. | Dead slot remains occupied. | Missing data preserves; messaging does not substitute for the ledger. |
| 3 | Prolonged host stall interrupts heartbeats. | Candidate could be observed incorrectly. | Ten-minute margin, all-pane ended gate, review observe logs, and revalidation. |
| 4 | Circus is unavailable. | No eventual cleanup. | Existing daemon lifecycle, status command, restart supervision; never launch-time fallback. |

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | Heartbeat persistence | (a) local ledger, (b) message history, (c) tmux metadata | (a) | (a) | No | Same-host durable evidence is independent of remote delivery. | `Resolved` |
| D-2 | Reap authorization | (a) two gates + ID revalidation, (b) one snapshot, (c) process retry | (a) | (a) | No | Destructive cross-session action needs corroboration and time-of-use identity. | `Resolved` |
| D-3 | Watcher lifecycle/cadence | (a) explicit Circus/60s, (b) launch auto-start, (c) external scheduler | (a) | (a) | No | Meets required isolation using existing integration. | `Resolved` |
| D-4 | Rollout mode | (a) observe default, (b) reap default, (c) permanently disabled | (a) | (a) | No | Creates operational evidence before kill authority. | `Resolved` |
| D-5 | Staleness threshold | (a) 10m, (b) 5m, (c) 30m | (a) | (a) | No | Conservative margin while preserving eventual recovery. | `Resolved` |
| D-6 | Launch boundary | (a) strict separation, (b) shared routine, (c) launch notification | (a) | (a) | No | Prevents return of launch-time authority. | `Resolved` |

### Decision Details

<a id="d-1"></a>

#### D-1: Heartbeat persistence — ✅ Resolved by Codex: (a) Local atomic ledger

**Context.** Existing heartbeats are sent to messaging, but no local consumer persists a staleness record. Reap authorization cannot depend on remote delivery semantics.

##### (a) Local atomic ledger under XDG state

**Pros:**
- Same-host durable evidence independent of messaging availability and retention.
- Supports schema and identity validation before a destructive decision.

**Cons:**
- Adds atomic-file lifecycle code.
- Storage failure delays cleanup.

##### (b) Read message-stream history

**Pros:**
- Reuses heartbeat transport without a local record.
- Could be visible across hosts.

**Cons:**
- Couples local kill authority to remote connectivity, retention, and consumer semantics.
- The current publisher deliberately treats messaging failure as non-fatal.

##### (c) Store timestamp in tmux metadata

**Pros:**
- Keeps data near the session.
- Avoids a separate ledger directory.

**Cons:**
- Couples corroboration to the tmux control path under evaluation.
- Makes absent metadata ambiguous.

##### Recommendation

> **Decision:** ✅ Resolved by Codex — (a) Local atomic ledger under XDG state
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-terra; ai-effort=medium; ai-profile=implement -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-1; decision-topic=heartbeat-persistence; governs=artifact:local-heartbeat-ledger; normalized-proposition=Persist reap heartbeats in an atomic XDG-state ledger; applicability=scope:managed-tmux-sessions; outcome-id=local-atomic-ledger; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-1; approval-actor=Codex; approval-date=2026-08-27; approval-commit= -->

Criteria 1 and 2 favor the robust host-local safety boundary. Atomic replacement and non-mocked round-trip tests mitigate write complexity; the remaining failure mode intentionally preserves the session and logs it. Confidence: high.

---

<a id="d-2"></a>

#### D-2: Reap authorization — ✅ Resolved by Codex: (a) Two gates plus ID revalidation

**Context.** One pane PID was insufficient evidence, and a name can be recreated between candidate evaluation and action.

##### (a) Both gates, then revalidate exact session ID

**Pros:**
- Requires independent evidence and prevents name-target races.
- Fails closed at both observation points.

**Cons:**
- Adds a second tmux/probe pass.
- Ambiguity delays cleanup to a later interval.

##### (b) Both gates from one snapshot, then kill by name

**Pros:**
- Fewer calls and smaller implementation surface.
- Better than process-only detection.

**Cons:**
- Retains a time-of-check/time-of-use race.
- Can target a newly recreated name.

##### (c) Retry process liveness without heartbeat

**Pros:**
- Avoids persistence work.
- Can identify a dead PID sooner.

**Cons:**
- Repeats the unsafe single-signal model.
- Cannot distinguish transient/unreadable process state from death.

##### Recommendation

> **Decision:** ✅ Resolved by Codex — (a) Both gates, then revalidate exact session ID
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-terra; ai-effort=medium; ai-profile=implement -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-2; decision-topic=reap-authorization; governs=artifact:reap-protocol; normalized-proposition=Authorize a reap only after both gates pass twice for one tmux session ID; applicability=scope:managed-tmux-sessions; outcome-id=two-gates-id-revalidation; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-2; approval-actor=Codex; approval-date=2026-08-27; approval-commit= -->

This is a one-way cross-session operation, so criteria 1 and 2 resolve toward robust revalidation. The small query cost is bounded to eligible candidates; the residual delay is safe. Confidence: high.

---

<a id="d-3"></a>

#### D-3: Watcher lifecycle and cadence — ✅ Resolved by Codex: (a) Explicit Circus watcher every 60 seconds

**Context.** Execution must be independent of every session launch and use the installed Circus integration.

##### (a) Explicit persistent Circus watcher, 60-second cadence

**Pros:**
- Keeps evaluation/kill authority outside launch paths.
- Reuses daemon restart supervision and provides clear operator controls.

**Cons:**
- Requires explicit setup before dead slots are reaped.
- Adds periodic tmux activity.

##### (b) Register automatically during launch

**Pros:**
- Requires no operator setup.
- Makes the watcher broadly available.

**Cons:**
- Couples one launch to another session's reaper lifecycle.
- Violates the required isolation boundary.

##### (c) Require an external operating-system scheduler

**Pros:**
- Avoids package lifecycle commands.
- Lets operators use native schedulers.

**Cons:**
- Does not follow the existing Circus integration.
- Has inconsistent setup and status behavior across platforms.

##### Recommendation

> **Decision:** ✅ Resolved by Codex — (a) Explicit persistent Circus watcher, 60-second cadence
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-terra; ai-effort=medium; ai-profile=implement -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-3; decision-topic=watcher-lifecycle-cadence; governs=artifact:circus-watcher; normalized-proposition=Run an explicitly started Circus-managed reaper every 60 seconds; applicability=scope:local-host; outcome-id=explicit-circus-60-seconds; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-3; approval-actor=Codex; approval-date=2026-08-27; approval-commit= -->

Criterion 2 favors an explicit lifecycle separation. Idempotent start/status commands mitigate setup; the poll is limited to one interval per minute. Confidence: high.

---

<a id="d-4"></a>

#### D-4: Rollout mode — ✅ Resolved by Codex: (a) Observe default with explicit `reap`

**Context.** This component will gain destructive authority but has no production evidence.

##### (a) `observe` default; explicit configuration for `reap`

**Pros:**
- Exercises the full predicate without termination.
- Makes destructive activation deliberate and reviewable.

**Cons:**
- Does not reclaim slots until an operator opts in.
- Requires log review and configuration editing.

##### (b) `reap` default

**Pros:**
- Restores automatic cleanup immediately.
- Has no rollout step after startup.

**Cons:**
- Grants kill authority before operational evidence.
- Violates the required safe initial rollout.

##### (c) Permanently disabled

**Pros:**
- Eliminates new automated kill authority.
- Needs no monitoring.

**Cons:**
- Does not solve permanent name squatting.
- Forces manual cleanup indefinitely.

##### Recommendation

> **Decision:** ✅ Resolved by Codex — (a) `observe` default; explicit configuration for `reap`
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-terra; ai-effort=medium; ai-profile=implement -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-4; decision-topic=rollout-mode; governs=artifact:stale-session-reaper-config; normalized-proposition=Default to observe-only and require explicit configuration for reap mode; applicability=scope:initial-deployment; outcome-id=observe-default; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-4; approval-actor=Codex; approval-date=2026-08-27; approval-commit= -->

Criteria 1 and 3 favor observe-first. Commands, candidate logs, and the defined `reap` value mitigate operational effort; delayed cleanup is bounded by explicit choice. Confidence: high.

---

<a id="d-5"></a>

#### D-5: Staleness threshold — ✅ Resolved by Codex: (a) 10 minutes

**Context.** Normal heartbeats occur about every 30 seconds; the threshold must tolerate ordinary delays without making recovery ineffective.

##### (a) 10-minute default

**Pros:**
- Allows about twenty missed normal intervals.
- Reclaims genuinely dead slots within a reasonable bound.

**Cons:**
- A dead slot remains occupied for at least ten minutes plus one interval.
- A severe host stall still needs the process/revalidation protections.

##### (b) 5-minute default

**Pros:**
- Reclaims sooner.
- Shorter retention window.

**Cons:**
- Only about ten normal intervals of tolerance.
- Less conservative against unknown transient behavior.

##### (c) 30-minute default

**Pros:**
- Very large stall tolerance.
- Further lowers heartbeat-absence risk.

**Cons:**
- Leaves dead slots unavailable for a long time.
- Weakens practical crash recovery.

##### Recommendation

> **Decision:** ✅ Resolved by Codex — (a) 10-minute default
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-terra; ai-effort=medium; ai-profile=implement -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-5; decision-topic=heartbeat-staleness-threshold; governs=artifact:stale-session-reaper-config; normalized-proposition=Use a ten-minute default heartbeat staleness threshold; applicability=scope:default-configuration; outcome-id=ten-minutes; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-5; approval-actor=Codex; approval-date=2026-08-27; approval-commit= -->

Criteria 2 and 3 favor this conservative long-lived default. One-minute cadence/configurability mitigate delay; process corroboration and ID revalidation bound long-stall risk. Confidence: medium.

---

<a id="d-6"></a>

#### D-6: Launch-time cleanup boundary — ✅ Resolved by Codex: (a) Strict separation

**Context.** Launch-time cleanup legitimately handles auxiliary state but must not regain termination authority.

##### (a) Keep reaper and launch cleanup separate

**Pros:**
- Makes it mechanically clear that launch code cannot end tmux sessions.
- Preserves independent failure domains and shipped cleanup behavior.

**Cons:**
- Duplicates limited tmux enumeration logic.
- Needs explicit non-interaction tests.

##### (b) Share a cleanup routine invoked by launch and Circus

**Pros:**
- Centralizes some enumeration code.
- May share logging/configuration helpers.

**Cons:**
- Lets launch code reach termination-capable logic.
- Blurs ownership and makes authority regressions easier.

##### (c) Have launch cleanup notify the watcher immediately

**Pros:**
- Could reduce recovery latency.
- Keeps the kill command in the watcher.

**Cons:**
- Still makes a launch cause another session's evaluation.
- Creates prohibited race-sensitive coupling.

##### Recommendation

> **Decision:** ✅ Resolved by Codex — (a) Keep reaper and launch cleanup separate
<!-- decision-record: chosen-option=(a); ai-family=codex; ai-model=gpt-5.6-terra; ai-effort=medium; ai-profile=implement -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-6; decision-topic=launch-cleanup-boundary; governs=artifact:cleanup-stale-sessions; normalized-proposition=Keep reaper execution and termination authority separate from launch-time cleanup; applicability=scope:session-launches; outcome-id=strict-separation; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-6; approval-actor=Codex; approval-date=2026-08-27; approval-commit= -->

This one-way safety boundary has broad lifecycle impact, so criteria 1 and 2 resolve toward strict separation. Isolated modules and explicit no-call tests mitigate duplication; no launch-time termination path remains. Confidence: high.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> 3. D-3: <approval or feedback>
> 4. D-4: <approval or feedback>
> 5. D-5: <approval or feedback>
> 6. D-6: <approval or feedback>
> - <enter feedback here>

## Open Questions

1. What exact runtime condition caused the prior pane-leader liveness observation to misidentify active wrappers as ended? Wrapper self-reload is correlated but unconfirmed. This does not block the design because it requires a second, independently persisted signal and revalidation.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <enter feedback here>
> - <enter feedback here>

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-08-27 | stale-session-reaper/D-1 | Codex | Local atomic heartbeat ledger; fresh-authoring, high confidence. |
| 2026-08-27 | stale-session-reaper/D-2 | Codex | Two-gate ID-revalidation protocol; fresh-authoring, high confidence. |
| 2026-08-27 | stale-session-reaper/D-3 | Codex | Explicit 60-second Circus watcher; fresh-authoring, high confidence. |
| 2026-08-27 | stale-session-reaper/D-4 | Codex | Observe-first rollout; fresh-authoring, high confidence. |
| 2026-08-27 | stale-session-reaper/D-5 | Codex | Ten-minute default threshold; fresh-authoring, medium confidence. |
| 2026-08-27 | stale-session-reaper/D-6 | Codex | Strict launch-time separation; fresh-authoring, high confidence. |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->
<!-- /doc:region name="decisions" -->
<!-- doc:region name="feedback_rounds" kind="append_only" -->
<!-- /doc:region name="feedback_rounds" -->
<!-- doc:region name="approval_log" kind="append_only" -->
<!-- /doc:region name="approval_log" -->
