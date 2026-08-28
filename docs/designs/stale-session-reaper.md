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
  - [D-7: Session-instance identity](#d-7)
  - [D-8: Final reap fence](#d-8)
  - [D-9: Staleness clock](#d-9)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Executive Summary

This design restores eventual cleanup of genuinely dead managed tmux sessions without allowing one session launch to terminate another session. A single Circus-managed watcher evaluates sessions asynchronously and authorizes a reap only when every pane leader is confirmed ended and a generation-matched local heartbeat is stale by same-boot monotonic time. Every incomplete, malformed, unavailable, or contradictory observation preserves the session. The watcher is not registered automatically by session-launch commands; an operator starts it independently, and its default mode records candidates without killing them. Launch-time `cleanup_stale_sessions()` remains bookkeeping-only.

## Problem Statement

Managed tmux session names can remain occupied after a wrapper or host crashes. A previous launch-time global sweep used a one-time pane-PID liveness observation and could terminate unrelated live sessions when that observation was wrong. The shipped emergency change correctly removed all tmux kill authority from launch-time cleanup, but leaves dead session-name slots unreclaimed.

## Design Overview

**Status:** stub — to be filled during/after implementation.

## Stale-Session Reaper

### Safety invariant

The reaper is the only new component authorized to invoke `tmux kill-session`. A tmux user option named `@ai_cli_session_generation` is the sole authoritative marker that a session is managed; the `_AI_SESSION_RE` name regex is never a managed-session classifier. The option holds a random, high-entropy generation token created once for each session instance. Legacy or tokenless sessions are observe-only and ineligible for reap until relaunched with a token.

In `reap` mode, the reaper may terminate only after both of these conditions hold for one captured tmux session ID and the same generation token: every ledger record and every tmux metadata read must agree on that token before either gate counts as satisfied.

1. tmux returns a valid session ID, generation token, pane ID, and positive leader PID for every pane, and the process probe confirms every captured pane/process identity has ended or is a zombie;
2. a readable, schema-valid local heartbeat record with the exact session name and generation token exists, its boot generation matches the current host, and its monotonic timestamp is older than the configured threshold.

This is conjunctive, not a score. A generation mismatch, missing token, or unreadable token on either side preserves the session. A live process with a stale heartbeat, an ended process with a fresh heartbeat, a missing/malformed record, a boot-generation mismatch, unavailable monotonic clock or boot identity, an invalid PID, an empty pane list, a pane or process identity mismatch, an exception, or an unreadable tmux/state response is ineligible. `observe` mode runs exactly this predicate but never kills.

A usable record additionally proves one uninterrupted lease epoch for its generation: the wrapper-supervised lease holder has acquired and verified the generation's exclusive lease before it writes the record, retains it while the wrapper remains live, and revokes the record while still holding the lease before any known loss of continuity. The holder is the one OS-lock owner and the only component that may authorise usable ledger writes; heartbeat subprocesses request writes from it and cannot acquire, release, or substitute for the lifetime lease. A record is unusable if the holder, its authenticated control channel, or its ownership verification is unavailable. This avoids relying on undocumented descriptor inheritance behavior.

### Periodic execution and lifecycle

The package will provide a session-reaper management command group with `start`, `stop`, `status`, and hidden/internal `run` commands, following the existing Circus watcher pattern. `start` idempotently registers one `stale-session-reaper` watcher with the existing local Circus daemon. `run` evaluates once, sleeps 60 seconds, and repeats. A malformed candidate or exception while evaluating a candidate preserves and logs that candidate, skips its remaining work for this interval, and continues with the next candidate (or completes normally if none remain). A kill that completed all its lease-held checks for an earlier candidate is not rolled back or otherwise affected by a later candidate failure.

At heartbeat-watcher startup, the wrapper starts one generation-scoped lease-holder process. The holder acquires and verifies the exclusive lease, acknowledges readiness to the wrapper, and only then accepts the first usable heartbeat write. The holder owns the lock handle for the wrapper generation; the wrapper supervises its control channel and the holder alone performs generation-conditional record writes and revocation. On normal wrapper exit, the holder first deletes or marks unusable its exact-generation record, acknowledges that revocation, and only then releases the lease. If controlled shutdown, the control channel, or adoption fails, the holder must revoke the usable record while it still owns the lease; if that cannot be proved, no usable record may be left or written. An abrupt wrapper or holder crash releases the OS lease automatically and stops further evidence writes; any retained record is evaluated only by the normal stale-heartbeat and full revalidation gates, never as proof that a live wrapper still owns the lease.

The generated wrapper currently has three direct self-`exec` transitions: stable-script hot reload and the refreshed-script and stable-script branches of self-update. The holder remains the owner through an `exec` only after the replacement script completes an authenticated adoption handshake for the same wrapper generation and the holder verifies that it still owns the lease. It must suspend heartbeat writes during that handshake. This protocol deliberately makes no claim that a POSIX file descriptor or any Windows lock handle survives `exec`; supported lock backends must prove the holder/control-channel behavior in real subprocess tests. If adoption cannot be completed, the holder must revoke the generation's usable record before releasing its lease; the replacement must acquire a fresh verified holder before it may write a usable record. Before an eligible `reap` candidate receives its final gate pass, the reaper attempts to acquire that same generation-bound lease exclusively and non-blockingly. If the lease is already held, cannot be acquired, cannot be verified, or its record/control state is ambiguous, the candidate is preserved for this cycle and is not retried during the same pass. The reaper holds its lease through the final gate pass and the `tmux kill-session` attempt, then releases it whether the kill succeeds or fails.

No session creation, resume, attach, `cleanup_stale_sessions()`, or `ai c`/`ai p`/`ai g`/`ai cx` path may call `start`, `run`, or the evaluator. The operator starts the watcher separately. A launch therefore cannot synchronously evaluate or terminate another session.

### Candidate evaluation and reap protocol

Each interval queries tmux for sessions carrying `@ai_cli_session_generation`, using a delimiter-safe format containing `#{session_id}`, `#{session_name}`, `#{pane_id}`, and `#{pane_pid}` plus the generation option. It groups by session ID, name, and generation token, then reads the corresponding ledger record and evaluates the invariant. `ProcessProbe.capture_identity(pid) -> ProcessIdentity | None` is the public process-birth capture contract. `ProcessIdentity` is an immutable typed value containing the backend kind and that backend's opaque process-birth marker: `ProcfsProbe` captures the parsed `/proc/<pid>/stat` start-time field, and `PsutilProbe` captures `psutil.Process(pid).create_time()` without converting it to another backend's unit. `None` means UNKNOWN, never a fabricated identity.

The initial snapshot captures the complete pane-ID-to-PID mapping, a process state, and identity for every present pane leader. A gone PID is a distinct `GONE` observation and has no identity; a zombie must have a non-UNKNOWN identity. The lease-held final snapshot must retain the exact pane-ID-to-PID mapping and match every observation: `GONE` must remain `GONE`, and a zombie identity must be exactly equal to its initial `ProcessIdentity`. A live, unreadable, or UNKNOWN observation at either pass, a `GONE`/zombie state transition, a pane replacement, PID reuse, process-identity mismatch, unavailable required identity, or changed token preserves the candidate. The evaluator never compares identities from different backends and never treats `None` as a match.

An eligible `observe` candidate is logged with its name, opaque tmux ID, generation token, monotonic heartbeat age, and reason, without a terminating command. For an eligible `reap` candidate, the worker acquires the generation-bound lease, re-queries the captured exact session ID, verifies the generation token and pane/process identities, and repeats both gates while holding the lease. Only then does it call `tmux kill-session -t <session-id>`, never by name. The immutable ID and generation token prevent a manually removed and recreated name from becoming the kill target. Failed lease acquisition, failed revalidation, changed ID/token/pane/process identity, or any tmux or lock error preserves the candidate.

After a successful kill, the worker may remove its ledger record only after tmux confirms that the captured ID no longer exists and only if the record still has the captured generation token. A wrapper may similarly clean up only its own generation's record on clean exit. It never removes a record after ambiguous status or failed termination. Records without a current candidate are ignored. A rename requires a fresh heartbeat carrying the current name before the renamed session can become eligible; until then it is preserved.

### Heartbeat recording

The generated session wrapper already invokes `ai internal publish-heartbeat` about every 30 seconds. At session start, it writes its generation token once to the tmux user option. It then starts and receives a verified-ready acknowledgement from the generation-scoped lease holder before the holder writes its first usable heartbeat record; the token is immutable for that session instance. The internal command validates its JSON, requests a holder-authorised atomic local-ledger write, and then retains its current best-effort messaging publication. Message delivery, retention, and consumers never authorize a local reap.

Each periodic heartbeat write refreshes the ledger's boot generation and monotonic timestamp; `recorded_at` remains a wall-clock field solely for operator-log readability. A generation-conditional ledger operation must refuse to replace or delete a record belonging to a different generation, and the holder must refuse a write if it cannot verify its lease, its control channel, or the token's current tmux metadata. The holder makes the XDG state directory with `pathlib`, writes and flushes a complete temporary file in that directory, then atomically replaces the final file. This guarantees atomically visible complete-file reads, not crash durability: where storage cannot establish durability, a record surviving a crash is not guaranteed. The safety protocol tolerates that loss or staleness by preserving the session and waiting for a fresh heartbeat, never by granting reap authority. After valid JSON is accepted, a local ledger-write failure is non-fatal: it logs the local failure, still attempts the existing best-effort `publish_heartbeat` call, exits non-fatally, and creates no reap authority. Logs use reason codes such as `heartbeat_missing`, `heartbeat_invalid`, `heartbeat_boot_mismatch`, `heartbeat_not_stale`, `generation_mismatch`, `lease_unavailable`, `lease_adoption_failed`, `pid_invalid`, `process_unknown`, `process_identity_mismatch`, `process_live`, `tmux_read_failed`, `revalidation_failed`, and `mode_observe`; they contain no prompt or agent content.

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

Records live beneath the existing XDG state home at `session-heartbeats/<encoded-session-name>-<encoded-generation-token>.json`, where both encodings are reversible and filesystem-safe. The schema is:

```json
{"version": 1, "session_name": "session-1", "generation_token": "random-high-entropy-token", "boot_generation": "current-host-boot-generation", "monotonic_recorded_at": 123456.789, "recorded_at": 1787860800}
```

`generation_token`, `boot_generation`, and `monotonic_recorded_at` are required locally written fields. `recorded_at` is an integer Unix timestamp written locally for logs only, not accepted from wrapper payload and never authoritative for reaping. The evaluator accepts a record only if it is a JSON object with this version, exact session-name and generation-token matches, a boot generation matching the current host, and a finite monotonic timestamp from the available current-boot monotonic clock. A record missing any required field, a legacy/tokenless record, a boot-generation mismatch, or unavailable monotonic clock/boot identity is unavailable evidence: preserve the session; for a boot mismatch specifically, wait for a fresh heartbeat under the current boot and never immediately reap. Every other shape fails closed.

## Integration

`session_script.py` continues its existing heartbeat call, creates the generation token, writes it to `@ai_cli_session_generation`, and starts/supervises the generation-scoped lease holder. Its stable-script hot reload and both self-update `exec` branches must run the adoption protocol above; none may bypass it. The internal handler adds holder-authorised, generation-conditional ledger persistence before its best-effort message publication; a local write failure does not suppress that publication attempt. `process_probe.py` adds the public `ProcessIdentity` type and `capture_identity()` contract, implemented by both existing probe backends. Existing event publication is otherwise unchanged. `process_manager.py` gains Circus registration/removal helpers beside existing watchers, using the existing XDG state directory and package-resolved executable. `main.py` dispatches the management and internal run commands. The metadata option, rather than any session-name pattern, is the sole authoritative managed-session marker; this includes valid indexed, custom, hyphenated, local, remote, and `cx` session names.

`cleanup_stale_sessions()` remains unchanged in authority: it may list tmux names solely for orphaned background-spare cleanup and stale terminal-profile bookkeeping, but must not import/call the evaluator, start Circus, read heartbeat records, or invoke `tmux kill-session`. Conversely, the watcher must not call `cleanup_stale_sessions()`, background-spare cleanup, or terminal-profile cleanup. This prevents a scheduling overlap from granting launch-time code termination authority.

## Implementation Phases

### Phase 1: Fail-closed evaluator

- **Scope:** Implement local heartbeat persistence, generation-scoped lease-holder/adoption behavior in `src/ai_cli/session_script.py`, the public process-identity capture contract in `src/ai_cli/process_probe.py`, and a standalone evaluator, without any launch-path integration.
- **Deliverables:**
  - Files created: `src/ai_cli/stale_session_reaper.py`, `tests/test_stale_session_reaper.py`.
  - Files modified: `src/ai_cli/main.py`, `src/ai_cli/session_script.py`, `src/ai_cli/process_probe.py`, `src/ai_cli/config.py`.
  - **Tasks + acceptance criteria:**
  - **T-1.1 Ledger persistence**
    - [ ] When `ai internal publish-heartbeat` receives valid JSON for a generation-marked session, the system shall atomically persist a locally timestamped record containing the exact session name, generation token, current boot generation, and current monotonic timestamp before best-effort messaging publication.
    - [ ] When a valid local, remote, indexed, custom, hyphenated, `c`, `g`, `p`, or `cx` session is created with `@ai_cli_session_generation`, the system shall classify it as managed from that option and not from a session-name regex.
    - [ ] When a generation-marked session exits cleanly, the system shall remove only the ledger record with its exact generation token; if it crashes instead, any remaining record shall not authorize a session with a different token.
    - [ ] When a wrapper starts, the system shall acquire and verify its generation-scoped lease holder before the holder writes any usable heartbeat record.
    - [ ] If JSON is invalid, the generation token is missing or cannot be verified against tmux metadata, or the record cannot be written, then the system shall create no usable record and shall not terminate a tmux session.
    - [ ] If the wrapper or holder cannot acquire, verify, supervise, or adopt its generation-bound lifetime lease, then the system shall create no usable heartbeat evidence and shall preserve the session.
    - [ ] When the generated wrapper takes its stable-script hot-reload `exec` branch or either refreshed-script or stable-script self-update `exec` branch, the system shall suspend usable heartbeat writes until the holder verifies adoption for the same generation; if verification fails, it shall revoke that generation's usable record before releasing the lease.
    - [ ] When a wrapper exits normally or its process crashes, the system shall respectively revoke its exact-generation usable record before releasing the lease, or release the OS lease automatically and prevent any further evidence writes; a retained crash record shall receive only the normal stale-heartbeat and full revalidation evaluation.
    - [ ] If local ledger persistence fails after valid JSON is accepted, then the system shall log the local failure, still attempt the existing best-effort `publish_heartbeat` call, and exit non-fatally without creating reap authority.
  - **T-1.2 Corroborated evaluator**
    - [ ] When every pane leader is confirmed ended or zombie and a generation-matched current-boot heartbeat is older than the threshold, the system shall mark that exact session ID provisionally eligible for lease-held revalidation.
    - [ ] When `ProcessProbe.capture_identity(pid)` captures a known identity for a present pane leader in both snapshots and the identities exactly match, the system shall permit that identity portion of the revalidation gate to pass.
    - [ ] If `ProcessProbe.capture_identity(pid)` returns `None` for a present pane leader in either snapshot, then the system shall treat the identity as UNKNOWN, preserve the candidate, and issue no kill command.
    - [ ] If the initial and final process identities differ for the same pane PID, then the system shall treat it as PID reuse, preserve the candidate, and issue no kill command.
    - [ ] When only pane liveness or only heartbeat staleness indicates death, the system shall preserve the session and issue no kill command.
    - [ ] If a generation token, PID, pane ID, process identity, record, tmux response, process-probe response, monotonic clock, boot identity, or configuration value is missing, malformed, unreadable, mismatched, or raises an error, then the system shall preserve the candidate and log a reason.
    - [ ] When a heartbeat record has a boot generation different from the current host, the system shall preserve the session, issue no kill command, and wait for a fresh heartbeat under the current boot.
    - [ ] When a generation-marked live wrapper holds its generation lease, the system shall preserve an otherwise eligible candidate for that cycle and issue no reap attempt.
  - **T-1.3 Revalidation**
    - [ ] When a candidate is eligible in `reap` mode and its generation-bound lease is acquired exclusively and non-blockingly, the system shall re-query its captured session ID, generation token, pane IDs, PIDs, and process identities and repeat both gates while holding the lease before `tmux kill-session -t <session-id>`.
    - [ ] If lease acquisition fails, its ID or token disappears/changes, a pane or process identity changes or is unavailable, it gains an unconfirmed pane, or either gate fails during revalidation, then the system shall issue no kill command and shall not retry the candidate in that pass.
  - **T-1.4 Integrated safety regression coverage**
    - [ ] When a real temporary heartbeat ledger, a generation token, and a generation-bound lease are exercised through controlled tmux and process adapters, the system shall issue exactly one ID-targeted kill only when both corroborating gates pass initially and during lease-held revalidation.
    - [ ] When a mutation or negative control removes either the process gate or the generation-matched heartbeat gate from that integrated round trip, the system shall issue zero kill commands.
    - [ ] When a pane is replaced, a PID is reused, a process identity changes, or a tokenless/foreign/renamed session lacks a fresh current-name heartbeat, the system shall preserve the candidate and issue zero kill commands.
    - [ ] When real wrapper and reaper subprocesses exercise startup, each of the three direct self-`exec` branches, failed adoption, normal exit, holder/wrapper crash release, and a reaper racing each transition, the system shall prove the required record-before-release ordering and zero unauthorised kill attempts on every supported lock backend.
    - [ ] When `ProcfsProbe` and `PsutilProbe` capture a process identity, the system shall expose the documented backend-specific typed value; when either backend cannot capture it or a controlled PID-reuse case changes it, the evaluator shall preserve the candidate.
  - **Exit gate:** Integrated real-temporary-ledger, generation-token, lease, and process-identity round trips plus controlled tmux/process adapters prove the positive and mutation-negative controls; real subprocess tests prove startup ordering, all three self-`exec` paths, failed adoption, normal exit, crash release, and reaper races on every supported lock backend; `pytest tests/test_stale_session_reaper.py -q` and focused `ruff check` pass; fresh-context review confirms no launch path calls the evaluator.

### Phase 2: Circus lifecycle and rollout

- **Scope:** Add independently invoked Circus management, configuration validation, documentation, and non-interaction regressions.
- **Deliverables:**
  - Files modified: `src/ai_cli/process_manager.py`, `src/ai_cli/main.py`, `src/ai_cli/config.py`, `README.md`, and focused process-manager/CLI tests.
  - **Tasks + acceptance criteria:**
  - **T-2.1 Independent lifecycle**
    - [ ] When an operator runs the documented start command, the system shall register exactly one Circus watcher running the entry point at a 60-second cadence.
    - [ ] When an operator runs the documented stop command for a registered watcher, the system shall remove that watcher and report success.
    - [ ] If stop cannot contact Circus or remove the watcher, then the system shall report failure and shall not scan or terminate a tmux session.
    - [ ] When an operator runs the documented status command, the system shall report whether the one registered watcher is running.
    - [ ] If status cannot contact Circus or obtain watcher state, then the system shall report failure rather than report the watcher healthy.
    - [ ] When an operator invokes `run` and an interval completes normally, the system shall sleep for the configured 60-second cadence before the next interval.
    - [ ] If one malformed candidate or an evaluator exception occurs during `run`, then the system shall preserve that candidate, log a reason, skip its remaining work for that interval, and continue evaluating other candidates or complete the interval normally.
    - [ ] When any launch command or `cleanup_stale_sessions()` runs, the system shall neither register the watcher nor execute the evaluator.
    - [ ] If Circus is unavailable or registration fails, then the start command shall report failure and shall not fall back to synchronous scanning or termination.
  - **T-2.2 Observe-first rollout**
    - [ ] Where mode is absent or `observe`, the system shall log a fully corroborated candidate and issue no kill command.
    - [ ] Where mode is explicitly `reap`, the system shall terminate only candidates passing initial and revalidation gates.
    - [ ] If mode or threshold is invalid, then the system shall preserve all sessions and log the configuration error.
  - **T-2.3 Authority regressions**
    - [ ] When launch-time cleanup is exercised against managed tmux listings, the system shall not invoke `tmux kill-session` or a reaper entry point.
    - [ ] When a candidate is process-live/heartbeat-stale or process-ended/heartbeat-fresh, the system shall issue zero kill commands in both cases.
    - [ ] When candidate A is eligible and killed, and candidate B in the same interval throws during evaluation, the system shall preserve candidate B and shall not affect candidate A's already-completed kill.
  - **T-2.4 Public documentation correction**
    - [ ] When Phase 2 documentation is completed, the system documentation shall correct README.md's Features table and every `stale_session_timeout` configuration example so they do not claim automatic or synchronous launch-time cleanup and instead describe the reaper's independent start command, observe-first default, and explicit reap opt-in.
    - [ ] When the public behavior correction is released, CHANGELOG.md shall contain an Unreleased entry describing the correction without rewriting released history.
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
| 7 | D-7 binds the sole managed marker and every ledger record to one generation token | - [ ] | |
| 8 | D-8 holds the generation lease through final identity/gate verification and ID-targeted kill | - [ ] | |
| 9 | D-9 uses current-boot monotonic age and preserves on boot/clock uncertainty | - [ ] | |
| 10 | Phase 1 integrated positive, mutation-negative, lease, and pane/process-identity ACs are covered | - [ ] | |
| 11 | Phase 2 start/stop/status/run success and failure ACs are covered | - [ ] | |
| 12 | Phase 1 publication-parity and Phase 2 public-documentation correction ACs are covered | - [ ] | |
| 13 | Lease holder acquires before first usable evidence and proves/fails closed across all three wrapper self-`exec` transitions | - [ ] | |
| 14 | `ProcessProbe.capture_identity()` has typed Procfs/Psutil implementations and UNKNOWN/mismatch preservation coverage | - [ ] | |
| 15 | Candidate-local exceptions preserve only the failed candidate and do not roll back earlier authorised kills | - [ ] | |

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | The exact liveness false-positive trigger is unconfirmed; wrapper self-reload is only correlated. | A process reading could be transiently wrong. | Generation-bound ledger evidence, wrapper-held lease, final identity revalidation, immutable ID target, and observe-first rollout. |
| 2 | Publisher or local storage fails. | Dead slot remains occupied. | Missing or non-durable data preserves; messaging does not substitute for the ledger. |
| 3 | Prolonged host stall interrupts heartbeats. | Candidate could be observed incorrectly. | Ten-minute margin, all-pane ended gate, review observe logs, and revalidation. |
| 4 | Circus is unavailable. | No eventual cleanup. | Existing daemon lifecycle, status command, restart supervision; never launch-time fallback. |

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | Heartbeat persistence | (a) local ledger, (b) message history, (c) tmux metadata | (a) | (a) | No | Same-host atomically visible evidence is independent of remote delivery. | `Resolved` |
| D-2 | Reap authorization | (a) two gates + ID revalidation, (b) one snapshot, (c) process retry | (a) | (a) | No | Destructive cross-session action needs corroboration and time-of-use identity. | `Resolved` |
| D-3 | Watcher lifecycle/cadence | (a) explicit Circus/60s, (b) launch auto-start, (c) external scheduler | (a) | (a) | No | Meets required isolation using existing integration. | `Resolved` |
| D-4 | Rollout mode | (a) observe default, (b) reap default, (c) permanently disabled | (a) | (a) | No | Creates operational evidence before kill authority. | `Resolved` |
| D-5 | Staleness threshold | (a) 10m, (b) 5m, (c) 30m | (a) | (a) | No | Conservative margin while preserving eventual recovery. | `Resolved` |
| D-6 | Launch boundary | (a) strict separation, (b) shared routine, (c) launch notification | (a) | (a) | No | Prevents return of launch-time authority. | `Resolved` |
| D-7 | Session-instance identity | (a) generation token in tmux metadata and ledger, (b) tmux server generation plus session ID, (c) name-only records with clean-exit deletion | (a) | (a) | No | Binds all reap evidence and the managed marker to one session instance. | `Approved` |
| D-8 | Final reap fence | (a) generation-bound lifetime lease plus exclusive final fence, (b) persistent two-phase claim with grace interval, (c) repeated probes and longer threshold | (a) | (a) | No | Prevents a live wrapper from publishing between final evidence read and kill. | `Approved` |
| D-9 | Staleness clock | (a) same-boot monotonic time plus boot generation, (b) wall time plus discontinuity detector, (c) wall time only | (a) | (a) | No | Avoids wall-clock jumps and preserves after reboot until republished. | `Approved` |

### Decision Details

<a id="d-1"></a>

#### D-1: Heartbeat persistence — ✅ Resolved by Codex: (a) Local atomic ledger

**Context.** Existing heartbeats are sent to messaging, but no local consumer persists a staleness record. Reap authorization cannot depend on remote delivery semantics.

##### (a) Local atomic ledger under XDG state

**Pros:**
- Same-host atomically visible evidence independent of messaging availability and retention.
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

The fail-closed requirement and host-local evidence boundary favor the robust host-local safety boundary. Atomic replacement and non-mocked round-trip tests mitigate write complexity; the remaining failure mode intentionally preserves the session and logs it. Confidence: high.

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

This is a one-way cross-session operation, so the fail-closed requirement and exact-ID targeting requirement resolve toward robust revalidation. The small query cost is bounded to eligible candidates; the residual delay is safe. Confidence: high.

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

The out-of-band lifecycle requirement favors an explicit lifecycle separation. Idempotent start/status commands mitigate setup; the poll is limited to one interval per minute. Confidence: high.

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

The fail-closed requirement and observe-first rollout requirement favor observe-first. Commands, candidate logs, and the defined `reap` value mitigate operational effort; delayed cleanup is bounded by explicit choice. Confidence: high.

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

The fail-closed requirement and eventual-recovery quality attribute favor this conservative long-lived default. One-minute cadence/configurability mitigate delay; process corroboration and ID revalidation bound long-stall risk. Confidence: medium.

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

This one-way safety boundary has broad lifecycle impact, so the launch-time separation and fail-closed requirements resolve toward strict separation. Isolated modules and explicit no-call tests mitigate duplication; no launch-time termination path remains. Confidence: high.

---

<a id="d-7"></a>

#### D-7: Session-instance identity — ✅ Approved — (a) Random generation token in tmux metadata and ledger

**Context.** A name-keyed heartbeat cannot prove identity across close, rename, recreation, or tmux-server restart. The same mechanism must also define which sessions are managed.

##### (a) Random generation token in tmux metadata and ledger

**Pros:**
- A high-entropy token created once per session instance is independent of name and tmux ID reuse.
- A tmux user option can simultaneously provide an authoritative managed marker and the token the reaper reads.
- Generation-conditional writes/deletes prevent an old wrapper or reaper from clobbering a newer instance.

**Cons:**
- Session creation and wrapper-heartbeat arguments must change together.
- Sessions created before the feature have no token and cannot be reaped automatically.

##### (b) Tmux server generation plus session ID

**Pros:**
- Uses tmux-native identity and avoids a random-token lifecycle.
- The reaper already queries the opaque session ID.

**Cons:**
- The design must derive and persist a portable, trustworthy tmux-server generation.
- Binding the heartbeat publisher to that generation adds tmux control-path reads to every writer startup.

##### (c) Keep name-only records and delete on clean exit

**Pros:**
- Smallest change to the proposed schema.
- Normal exits remove most stale records.

**Cons:**
- Crashes, forced exits, and EXIT-trap failures are precisely the cases this feature targets and still leave stale authority.
- Cleanup and recreation can race, allowing an old instance to delete or authorize against a new one.

##### Recommendation

> **Decision:** ✅ Approved — (a) Random generation token in tmux metadata and ledger
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-7; decision-topic=session-instance-identity; governs=artifact:managed-session-marker-and-heartbeat-ledger; normalized-proposition=Bind managed-session identity and heartbeat evidence to one random generation token; applicability=scope:managed-tmux-sessions; outcome-id=generation-token-metadata-ledger; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-7; approval-actor=Sergei; approval-date=2026-08-28; approval-commit= -->

The approved token binds destructive evidence to an instance rather than a naming convention, and the tmux option also supplies the sole managed-session marker. A single session-creation helper and end-to-end tests mitigate the coordinated-change cost; tokenless legacy sessions remain observe-only/ineligible, so the migration limitation fails closed.

---

<a id="d-8"></a>

#### D-8: Final reap fence — ✅ Approved — (a) Generation-bound lifetime lease plus exclusive final fence

**Context.** Immediate double-checking still leaves a final heartbeat/process read-to-kill window. The protocol needs a rule for concurrent heartbeat, pane replacement, and PID reuse.

##### (a) Generation-bound lifetime lease plus exclusive final fence

**Pros:**
- A live or merely stalled wrapper retains an OS-managed lease continuously, so a false process probe cannot reach the kill.
- Process crash releases the lease automatically; the reaper can hold an exclusive lease across the final read and kill.
- Generation binding prevents an old reaper from fencing or deleting a newly recreated session.

**Cons:**
- The current once-per-30-second subprocess must become or acquire a wrapper-lifetime lease holder.
- Cross-platform lock acquisition, inheritance, and crash-release behavior need non-mocked tests.

##### (b) Persistent two-phase reap claim with a grace interval

**Pros:**
- A claim revision plus one heartbeat interval gives an ordinary live publisher time to cancel.
- Circus restart can conservatively abandon or resume persisted claim state.

**Cons:**
- A host/wrapper stall can exceed any finite grace and resume after the final check.
- It adds cleanup state without fully eliminating the last observation-to-kill edge.

##### (c) Rely on repeated probes and a longer threshold

**Pros:**
- Minimal new state and coordination.
- Reduces race likelihood statistically.

**Cons:**
- Does not close the final TOCTOU; it only makes it less frequent.
- Cannot satisfy the exact “before being reaped” heartbeat requirement under an adversarial interleaving.

##### Recommendation

> **Decision:** ✅ Approved — (a) Generation-bound lifetime lease plus exclusive final fence
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-8; decision-topic=final-reap-fence; governs=artifact:generation-bound-lease-protocol; normalized-proposition=Require a generation-bound wrapper lifetime lease and reaper-held final exclusive fence; applicability=scope:reap-mode; outcome-id=generation-bound-lifetime-lease; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-8; approval-actor=Sergei; approval-date=2026-08-28; approval-commit= -->

The approved lease closes the last read-to-kill edge because a live wrapper holds the same generation's lease continuously. A dedicated lock adapter and non-mocked cross-platform subprocess tests mitigate the lifecycle and portability costs; any failure to acquire, inherit, or verify the lease preserves the session.

---

<a id="d-9"></a>

#### D-9: Staleness clock — ✅ Approved — (a) Same-boot monotonic time plus boot generation

**Context.** A forward wall-clock correction can create artificial age. The ledger needs a portable rule for process restart and host reboot.

##### (a) Same-boot monotonic time plus boot generation

**Pros:**
- Monotonic elapsed time is immune to NTP/manual wall-clock corrections and suspend-related wall jumps.
- A boot-generation mismatch has a simple fail-closed meaning: preserve until a new heartbeat arrives.

**Cons:**
- Boot identity needs a portable abstraction across Linux, macOS, and Windows.
- Records from before reboot cannot authorize a reap until republished.

##### (b) Wall time plus a persisted clock-discontinuity detector

**Pros:**
- Keeps human-readable Unix timestamps as the primary record.
- Can preserve across reboot when no discontinuity is detected.

**Cons:**
- Correct discontinuity detection across process restart, suspend, and manual changes is itself complex state.
- An undetected forward step recreates the false stale signal.

##### (c) Wall time only with future-date rejection

**Pros:**
- Matches the current proposed schema and is portable.
- No boot-state dependency.

**Cons:**
- Handles backward movement only; forward movement can authorize a premature reap.
- The safety argument depends on ordinary clock behavior rather than a fail-closed invariant.

##### Recommendation

> **Decision:** ✅ Approved — (a) Same-boot monotonic time plus boot generation
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=stale-session-reaper/D-9; decision-topic=staleness-clock; governs=artifact:heartbeat-ledger-clock-fields; normalized-proposition=Use same-boot monotonic elapsed time and boot generation for reap staleness; applicability=scope:heartbeat-evaluation; outcome-id=same-boot-monotonic-clock; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=stale-session-reaper/D-9; approval-actor=Sergei; approval-date=2026-08-28; approval-commit= -->

The approved clock makes wall-clock adjustments non-authoritative and assigns reboot a simple preserve-until-republished outcome. A platform clock/boot-identity adapter mitigates portability; an unavailable adapter result is UNKNOWN and therefore cannot grant reap authority.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> 3. D-3: <approval or feedback>
> 4. D-4: <approval or feedback>
> 5. D-5: <approval or feedback>
> 6. D-6: <approval or feedback>
> 7. D-7: <approval or feedback>
> 8. D-8: <approval or feedback>
> 9. D-9: <approval or feedback>
> - <enter feedback here>

## Open Questions

1. What exact runtime condition caused the prior pane-leader liveness observation to misidentify active wrappers as ended? Wrapper self-reload is correlated but unconfirmed. This does not block the design because generation-bound corroboration, a wrapper-held lease, and lease-held identity revalidation do not depend on that root cause being confirmed.

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
| 2026-08-28 | stale-session-reaper/D-7 | Sergei | Approved (a) random generation token in tmux metadata and ledger; matches the AI recommendation. |
| 2026-08-28 | stale-session-reaper/D-8 | Sergei | Approved (a) generation-bound lifetime lease plus exclusive final fence; matches the AI recommendation. |
| 2026-08-28 | stale-session-reaper/D-9 | Sergei | Approved (a) same-boot monotonic time plus boot generation; matches the AI recommendation. |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->
<!-- /doc:region name="decisions" -->
<!-- doc:region name="feedback_rounds" kind="append_only" -->
<!-- /doc:region name="feedback_rounds" -->
<!-- doc:region name="approval_log" kind="append_only" -->
<!-- /doc:region name="approval_log" -->
