---
title: "Session maintenance can terminate unrelated live mosh transports"
category: bugs
tags: [session, mosh, tmux, process-hygiene]
status: fix-verified
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

Scope signals: three unrelated subsystems — no; new shared abstraction — no;
public contract — no; repository boundary — no; broader known pattern — yes.
The safe ownership rule is already established by the generation-fenced tmux
reaper. Removing signal authority from one implicit command is contained,
reversible, and sufficient; redesign criteria are not met.

## Fix

`ai ps cron` remains available for stale transport-file cleanup and remote
inventory refresh, but it can no longer call `auto_clean_orphans`. Only the
explicit, user-confirmed `ai ps clean` path can signal a scored process.
Consequently, session launch, replacement, restart, and exit transitions have
no route through heuristic process scoring to another session's process.

## Verification

The frozen sibling-survival regression was RED before the production edit
because the real sibling exited with status `-15`. It was GREEN after the
guard, RED for the same reason when only the production guard was temporarily
reversed, and GREEN again after restoration.

The complete process-hygiene suite passed (`81 passed`), as did the existing
generated-script and clean-exit checks (`2 passed`). The repository hard gate
reported `29 failed, 2824 passed, 15 skipped`; an untouched archive of the base
revision reproduced the same 29 failures plus two expected failures caused by
the archive having no Git index. The shared failures are unrelated macOS
sandbox, shell-startup, native-loader, process-tree, timestamp, and real-tmux
test-harness failures.

## Lessons learned

A score is suitable for prioritizing an operator's review, not for proving
process ownership. Implicit lifecycle hooks must not gain host-wide signal
authority from heuristic evidence, especially evidence collected on the
opposite side of a network connection.

## Fix log

| Date | Change | Notes |
|---|---|---|
| 2026-09-10 | Regression and causal fix | Implemented with high effort because the recurring symptom had several independent historical mechanisms. |
