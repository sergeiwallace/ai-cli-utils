---
title: "Vanished local sessions retained a running record"
category: bug
tags: [bug, sessions, lifecycle, reconciliation]
status: fixed
template_version: "bug-1.0.0"
---

# Vanished local sessions retained a running record

## Summary

<!-- doc:region name="summary" kind="replaceable" -->

A local session could disappear while its registry record retained
`run_state = running`. Boot replay could subsequently treat it as interrupted
work. A legacy record with neither `run_state` nor `ended_at` also silently
appeared running.

<!-- /doc:region name="summary" -->

## Reproduction

<!-- doc:region name="reproduction" kind="replaceable" -->

Register and mark a local run running, retain the current boot ID, then remove
both its tmux session and processes without calling the exit endpoint. Reading
the session list, ticking the publication worker, or running the stale-session
worker left the record running. The regression uses temporary storage, a fake
tmux boundary, and controlled process observations. Additional tests terminate
real child processes by SIGHUP, SIGTERM and SIGKILL without an exit callback.

<!-- /doc:region name="reproduction" -->

## Root Cause

<!-- doc:region name="root_cause" kind="replaceable" -->

`session_run_state.mark()` durably stores transitions only when called. A killed
wrapper or a vanished tmux server cannot reliably call it. The supervisor's EXIT
cleanup releases resources; it is not an independent observer of its own death.
There was no registry liveness reconciliation between launch and boot replay.

The local tmux probe also treated every nonzero status as absence, including
permission and protocol errors. The reconciler requires confirmed absence, so
the probe now recognizes missing-session/server diagnostics and preserves
unrecognized failures as unknown.

`StaleSessionReaper.evaluate_once()` enumerates existing, token-marked tmux panes.
A session absent from tmux cannot become a candidate, and the evaluator does not
read run-state records. `session_migrate._apply_iterm2()` renamed a restore record
and recipe while retaining its lifecycle fields. Migration's process/tmux checks
proved the session stopped without settling or refusing a contradictory running
record. Finally, `run_state()` inferred running from missing lifecycle fields.

The available read-only operational evidence does **not** identify the signal,
window action, or process failure that ended the reported run. It establishes an
unrecorded end, not which interruption caused it. The reaper log ends with live
process observations at 2026-11-10 13:08:26 EST (lines 59563–59564). However,
`circus.log` records the worker stopping at 13:08:57 (line 1306), a new master at
13:47:46 (line 1310), and the reaper starting at 13:48:26 (line 1312).
The hypothesis that the scheduler never restarted is therefore rejected. Routine
live-process observations now use DEBUG, explaining why missing routine log
lines alone cannot prove the worker stopped. Even a healthy worker could not
repair a missing-session registry record.

There was also a startup gap: `_launch_recipe()` calls `_do_session_launch()`
directly, whereas reaper startup lived in the outer engine CLI callback. Boot
replay therefore bypassed the scheduling hook. Circus watcher registration is
in memory; `_ensure_circusd()` writes a configuration containing only the master
section. A reboot requires registration again. Scheduling from detached launch
closes this gap without adding another daemon or changing boot policy.

<!-- /doc:region name="root_cause" -->

## Scope of Fix

<!-- doc:region name="scope_of_fix" kind="replaceable" -->

Reuse the existing worker and registry transaction/safety store. No new daemon,
configuration switch, registry schema version, or restart authority is needed.
`boot_veto()` retains its existing policy and changed-boot behavior.

Acceptance criteria:

- When a local record from the current boot has neither a tmux session nor a
  live launcher, wrapper or agent, reconciliation shall persist an unknown exit
  with check timestamps and evidence, and emit one structured reconciliation log.
- While boot identity or liveness is unavailable, reconciliation shall preserve
  the record. Records from another boot or host and remote records shall remain
  unchanged.
- When legacy lifecycle fields are missing, readers shall report unknown rather
  than infer running; same-boot absence shall still be reconciled.
- When migration encounters a running restore record, it shall refuse before
  moving artifacts and identify the command that performs reconciliation.

<!-- /doc:region name="scope_of_fix" -->

## Fix

<!-- doc:region name="fix" kind="replaceable" -->

`session_reconcile.reconcile()` checks the owning host, equal nonempty boot IDs,
exact tmux liveness, and local processes, then updates only unchanged records
under the registry lock. Slow probes cannot hold up a new launch or exit. A 60-second
startup grace protects a newly marked run. Unknown inputs preserve evidence.
The exit uses the same `exit`/`supervisor_exit` evidence shape as `sessions mark`,
increments the transition sequence, queues publication and retains the record.
Registry-write failure falls back to the existing deny-boot store.

The existing reaper invokes reconciliation independently of tmux reaping mode on
each 60-second tick. List, publication (even when publishing is disabled), single
enrolment and successful stop operations also reconcile. Detached replay schedules
the existing reaper, and local launches of every engine schedule it. `ai ls`
already lists live tmux sessions directly; bulk enrolment also discovers live
tmux panes rather than trusting stored run state.

| Ending path | Handling |
|---|---|
| Normal wrapper exit or successful managed stop | Existing exit persistence; reconciliation covers missing callbacks. |
| tmux session/server killed; terminal closes and ends the wrapper | Missing tmux and processes are settled on the next worker tick. A detached live session is preserved. |
| SIGHUP, SIGKILL, wrapper/supervisor crash | Same missing-process reconciliation; surviving agents or wrappers veto it. |
| Failed exit registry write | Existing durable safety evidence is retained and recovered. |
| Migration of a stopped session | A contradictory running record is refused before moves. |
| Power loss, kernel panic, forced power-off | Different-boot evidence is preserved for the existing boot authority. |

With a healthy worker and readable probes, a newly started run that immediately
vanishes is settled within 120 seconds plus probe/transaction time; an older run
is settled on the next tick. A failed scheduler can also be corrected by a reader
invocation. No periodic observer can distinguish a session that ended immediately
before power loss from one killed by that power loss without additional durable
evidence. Rewriting records after a changed boot would violate interruption
recovery, so this fix deliberately preserves that evidence. It does not promise
unconditional recovery when the scheduler, storage or liveness probes are unavailable.

<!-- /doc:region name="fix" -->

## Verification

<!-- doc:region name="verification" kind="replaceable" -->

Before production changes, `uv run pytest tests/test_session_reconcile.py -q`
reported 9 failed and 8 passed. Failures showed records remaining running through
all five observation paths, legacy fallback, and three real process interruptions.
The same regressions pass after the fix. Migration coverage asserts refusal and
byte-for-byte preservation of the fixture's artifacts. Existing boot tests check
that a running record from the previous boot remains eligible under its existing
policy. Final repository gate results are recorded in the change's handoff.
Three additional tmux-error regressions failed before tightening the probe and
pass afterward; an error status must never manufacture an exit.

The final reconciliation module run passed all 29 tests. A focused run covering
reconciliation, registry, restore, boot replay, dispatch, run-state, commands,
publication and migration passed 391 tests.

`uv run pytest` was attempted in the restricted macOS workspace and stopped after
repeated host-dependent failures. A diagnostic serial run identified
`test_given_a_grandchild_in_a_group_of_its_own_when_the_tree_is_reaped_then_it_is_gone_too`
failing because process enumeration was denied with
`PermissionError: [Errno 1] Operation not permitted (originated from sysctl() malloc 1/3)`.
Real tmux and the complete repository gate require verification outside this
sandbox; no check was disabled to manufacture a green result.

The serial exit-persistence suite finished with 29 passed and four failures:
the permanent-failure deadline checks for `zsh-exit` and `zsh-hang`, transient
CLI recovery for bash, and verified-installation recovery for bash. The first
two exceeded their elapsed-time bounds; the latter two exhausted a persistence
deadline or the subprocess timeout. Their underlying cause remains unverified;
the exit-persistence implementation and these assertions were left unchanged.

<!-- /doc:region name="verification" -->

## Lessons Learned

<!-- doc:region name="lessons_learned" kind="replaceable" -->

Exit persistence cannot observe an uncatchable death. A durable running flag
requires an independent observer, and a cleanup worker that enumerates only
surviving resources cannot find resources already gone. Tests must cover both
missing resources and live siblings, as well as preserve the evidence needed
after a reboot.

<!-- /doc:region name="lessons_learned" -->

## Fix Log

<!-- doc:region name="fix_log" kind="append_only" -->

| Date | Result |
|---|---|
| 2026-11-11 | Reproduced the missing observer through CLI and periodic worker entry points before changing production code. |
| 2026-11-11 | Added same-boot reconciliation, migration refusal, explicit legacy unknown state, and scheduler coverage for detached replay and other engines. |

<!-- /doc:region name="fix_log" -->
