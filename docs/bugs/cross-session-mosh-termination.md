---
title: "Session maintenance can terminate unrelated live mosh transports"
category: bugs
tags: [session, mosh, tmux, process-hygiene]
status: fix-implemented
severity: P1
template_version: "bug-1.0.0"
---

# Session maintenance can terminate unrelated live mosh transports

## Symptoms

When a managed agent exits or restarts, several unrelated remote terminal
connections can return to their local shells at once even though their tmux
sessions are independent.

## Environment and reproduction

The incident occurred with multiple macOS mosh clients connected to managed
tmux sessions on a Linux host. Host logs were unavailable, so the exact process
list and ages from the incident could not be preserved.

The causal path is deterministic in the current source. Scoring a typical live
`mosh-server` command with a local-client result of empty, an age of 25 hours,
and an unrelated live tmux session produces:

```text
score=80 threshold=80 verdict=orphaned detail=no active client, age > 24h, no matching tmux session
```

The regression starts a real sibling process, feeds that exact classification
to `ai ps cron`, and observes the subprocess receive SIGTERM on the unfixed
code.

## Root cause analysis

```text
a long-running agent exits normally
  -> its supervisor starts a replacement child
  -> the child runs `ai ps cron` during startup
  -> host-wide scoring mistakes live mosh servers for orphans
  -> cron sends SIGTERM to every local score-at-threshold process
  -> unrelated mosh clients exit to their local shells
```

`process_hygiene._run_lsof_udp()` only recognizes local `mosh-client`
processes. On the remote server the clients run on other machines, so their UDP
ports cannot appear in that local process listing. A normal mosh-server command
also does not contain the tmux session name, making both negative signals
incapable of proving abandonment. Age supplies the final points needed for the
automatic kill threshold.

The generated session child invokes `ai ps cron` before each agent launch. A
normal agent exit replaces the entire child, so the implicit cleanup reruns
during the reported exit/relaunch transition.

### Hypothesis ledger

| Hypothesis | Check | Result |
|---|---|---|
| Clean-exit tmux teardown targets a sibling | Traced the supervisor variable scopes | Rejected: teardown uses the baked `tmux_session`; transcript resolution mutates child-only `session_id` |
| The heartbeat reaper kills a live sibling | Read its lease, process-state, generation, and atomic tmux-fingerprint gates | Rejected for this path: every failed revalidation makes the kill unreachable |
| Session-start process hygiene kills live sibling transports | Score reproduction plus a real subprocess regression through `cmd_ps cron` | Confirmed: sibling exited with status `-15` |

## Scope-of-fix decision

The initial cron-only change contained the reported incident but was not sufficient to eliminate
the broader class. A complete authority inventory found other launch-time and explicit kill paths
that trusted a score, tmux name, or PID without immutable ownership proof. The remediation therefore
covers every identified path: implicit cleanup is non-destructive, tmux kills are generation-fenced,
and PID-based explicit commands revalidate captured process identity immediately before signalling.
Independent re-audit is still required before marking the class-level fix verified.

## Fix

`ai ps cron` remains available for stale transport-file cleanup and remote
inventory refresh, but it can no longer call `auto_clean_orphans`. Only the
explicit, user-confirmed `ai ps clean` path can signal a scored process, and it
must match the PID and creation time captured during inventory.

Launch-time stale-session cleanup no longer signals background helpers. Quota scraping allocates a
unique tmux name and cleans up only after atomically matching its captured opaque session ID and
generation token. Sandbox recreation refuses tokenless collisions and applies the same atomic
identity fence. Tunnel and browser state records now include PID, creation time, executable,
command, and port; stale or mismatched records are removed without touching the live PID holder.

## Verification

Class-wide remediation adds real-process survival regressions for hyphenated launch cleanup,
process-inventory identity mismatch, and legacy tunnel/browser PID records, plus real isolated-tmux
coverage for tokenless sessions and generation changes. The implementation is awaiting the
separate independent re-audit before this record returns to `fix-verified`.

The frozen sibling-survival regression was RED before the production edit
because the real sibling exited with status `-15`. It was GREEN after the
guard, RED for the same reason when only the production guard was temporarily
reversed, and GREEN again after restoration.

The focused class-level regression suite passes. A full repository run remains
blocked in the restricted test environment by pre-existing shell-startup,
native-loader, process-tree, timestamp, and real-tmux harness failures; an
untouched archive of the base revision reproduces those categories. Independent
verification should rerun the full suite in the repository's normal test
environment.

## Lessons learned

A score is suitable for prioritizing an operator's review, not for proving
process ownership. Implicit lifecycle hooks must not gain host-wide signal
authority from heuristic evidence, especially evidence collected on the
opposite side of a network connection.

## Fix log

| Date | Change | Notes |
|---|---|---|
| 2026-09-10 | Regression and causal fix | Implemented with high effort because the recurring symptom had several independent historical mechanisms. |
