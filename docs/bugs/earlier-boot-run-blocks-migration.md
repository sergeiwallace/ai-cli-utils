---
title: "After a reboot, a session whose record still says running can be neither migrated nor launched"
category: bug
tags: [bug, sessions, migration, reboot, run-state]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# After a reboot, a session whose record still says running can be neither migrated nor launched

## Founding Ask Coverage

The ask: a session whose tmux session and process are gone after a host reboot must be
migratable and launchable, and the root causes of its wrong run status must be fixed or filed.
This fix covers the migration preflight, which refused such a session forever. How
`ai sessions list` shows a record from an earlier boot is a separate gap, recorded under
Lessons Learned.

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-14

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

After a host reboot, `ai c NEW` for a session whose migration OLD -> NEW was recorded
`finished` without `verified` re-planned the migration and refused:

```text
refusing to launch NEW: the migration OLD -> NEW is partial; parts left: migration (failed:
could not re-plan: session run state is still running: liveness is unprovable; inspect the
launcher, tmux and process evidence); finish it with `ai migrate-session OLD NEW --finish -r REPO`
```

`ai migrate-session OLD NEW --finish` then refused the same way and skipped every part with
`preflight refused`. `tmux has-session` found no such session, and no agent process ran.
`ai sessions list` still showed the session as `running`, with a `changed` stamp from before
the reboot.

## Environment

macOS (any host where `kern.bootsessionuuid` or the Linux `boot_id` changes on reboot), with
the session recorded in the iTerm2 restore registry with `relaunch = "never"`, so the boot
relaunch never replays it.

## Reproduction Steps

1. Run a session, so its registry record says `run_state: running` and carries this boot's id.
2. Reboot the host. The restore sweep records an `exit` with cause `host_reboot` and sets
   `ended_at`, but leaves `run_state` as `running`.
3. Run `ai migrate-session OLD NEW --finish`. It refuses: liveness is unprovable.

`tests/test_session_reconcile.py` rebuilds this with a record whose `boot_id` is from an
earlier boot, fake tmux and an empty process table, and calls the preflight.

## Root Cause Analysis

The migration preflight (`_plan_iterm2`) refuses any restore record whose `run_state` is
`running`, after giving `session_reconcile.reconcile()` a chance to settle it. Reconcile
settles only records from the current boot. It leaves a record from an earlier boot unchanged
on purpose: no observer can tell a session that ended just before a power loss from one the
power loss killed, and the boot relaunch needs that evidence to decide whether to replay it
(`docs/bugs/stale-running-records.md`).

So for a record from an earlier boot nothing ever changes `run_state`. The boot relaunch
records a decision but, under `relaunch = "never"`, launches nothing. The restore sweep
records an `exit` but writes only `exit` and `ended_at`, while `run_state()` reads the
`run_state` field first. The preflight's `liveness_blocker` then had no branch for an earlier
boot, fell through to its last line, and reported "liveness is unprovable" even though tmux
and the process table had both answered that nothing was running.

## Prior Fix Attempts

None. The earlier stale-record fix (`stale-running-records.md`) covered same-boot records only.

## Fix

`liveness_blocker` now returns `None` when its owning host proves a record from an earlier
boot gone: the record's `owning_host` is this host, its `boot_id` differs from this boot's,
and tmux and the process table both answer that nothing is running. No process survives a
reboot, so that record cannot be live. The preflight proceeds and the migration renames the
record. The record itself is not rewritten, so the boot relaunch keeps its evidence.

Every other case still refuses, and now says which probe failed: a live tmux session or
process, a probe that could not answer (`the tmux probe could not answer`, `the process
table probe could not answer`), or a record owned by another host.

## Verification

New regression tests in `tests/test_session_reconcile.py`:

- an earlier-boot record proved gone plans the `iTerm2 restore record` rewrite and leaves the
  registry bytes unchanged (failed before the fix with "liveness is unprovable");
- an earlier-boot record with live tmux, an unanswered tmux probe, an unreadable process table
  or another owning host still refuses, naming the reason (three of the four failed before the
  fix, because the message did not name the reason).

The existing same-boot preflight and reconcile tests pass unchanged.

## Lessons Learned

- A record's run status lives in two places: `exit`/`ended_at`, written by the restore sweep
  and `ai iterm2 sessions -x`, and `run_state`/`supervisor_exit`, written by the supervisor
  and reconcile. A writer of only the first leaves readers of the second seeing `running`.
- `ai sessions list` still shows an earlier boot's record as `running`. Showing that it is from
  an earlier boot, without rewriting its evidence, is tracked separately.
- A refusal that ends "inspect the evidence" must name what the evidence said.

<!-- /doc:region name="summary" -->
