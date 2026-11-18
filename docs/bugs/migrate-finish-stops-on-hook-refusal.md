---
title: "migrate-session --finish stops when one hook refuses its preflight"
category: bug
tags: [bug, sessions, migration, hooks]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# migrate-session --finish stops when one hook refuses its preflight

## Founding Ask Coverage

`N/A`: this fix was requested through a relayed summary, not a recorded founding ask. The
ask as relayed: when one hook refuses, `ai migrate-session OLD NEW --finish` must still do
every other part, report that hook as pending, and exit 1. A fresh migration must still
refuse before its first write.

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-11

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

A session whose slot had already moved to NEW could not be finished. Each
`ai migrate-session OLD NEW --finish`, and the finish `ai c NEW` runs at launch, stopped with
exit 2 and the message `hook <name> (<path>) refused the migration (exit 4): ...`. Every part
in the table read `skipped: preflight refused`, so the Beads issues, launcher state and other
OLD-keyed leftovers stayed undone while the one hook kept refusing.

## Environment

Any host with at least one hook file in `migrate-session.d/` whose `--dry-run` exits non-zero
for a pair whose slot has already moved. One measured case: a mail hook refused while it could
not prove the board identity, and the launch-time finish logged
`migration OLD -> NEW not finished: hook ... refused the migration (exit 4)`.

## Reproduction Steps

1. Migrate a slot with `ai migrate-session OLD NEW -y` while one Beads store is unreadable, so
   the pair is recorded but not finished.
2. Add an OLD-keyed launcher state file, for example `gg-reload-c-OLD` in the state directory.
3. Declare two hooks: one that exits 4 on `--dry-run` and one that succeeds.
4. Run `ai migrate-session OLD NEW --finish -y`. It exits 2, re-keys no issue, moves no state
   file and runs neither hook.

## Root Cause Analysis

`plan_hooks` in `src/ai_cli/session_migrate_hooks.py` raised `HookRefusal` at the first hook
that exited non-zero, and `_plan_hooks` in `src/ai_cli/session_migrate.py` turned it into
`MigrationError`. `finish_session` calls `_plan_hooks` before any write, so one refusing hook
refused the whole finish.

That rule is right for a fresh migration: nothing has moved, and the refusal keeps it that way.
For `--finish` it protects nothing. The slot has already moved, and refusing only adds every
other leftover to the work still undone.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11-11 | Repeating the launch-time finish on each relaunch | The same hook refused each time, so the finish never converged |

## Fix

`plan_hooks` takes an optional `refused` list. With it, a hook that exits non-zero on
`--dry-run` is appended there and left out of the plan, and the remaining hooks are still
planned. Only `finish_session` passes it, through `_plan_hooks(..., pending=result)`. Each
refused hook becomes a `hook <name>` part with the warning
`hook <name> (<path>) NOT run: it refused its preflight (exit N): <stderr> -- pending; re-run the same command to finish`.
The parts table then reads `hook <name> | failed: ... pending ...`, the run counts as failed,
no `finished` ledger row is written, and the command exits 1. A later `--finish` plans that
hook again, while a hook that reports `migrate-status: done` stays skipped.

Unchanged: a fresh `migrate_session` still refuses before its first write, and under `--finish`
a malformed hook file or an invalid `migrate-status` line still refuses the run.

## Verification

- [x] `tests/test_session_migrate_hooks.py::test_given_a_moved_slot_and_a_hook_refusing_its_plan_when_finished_then_the_rest_runs_and_it_is_pending`
  drives the reproduction above through the CLI. It asserts that the Beads issue is re-keyed,
  the state file moved, the other hook ran live, the refusing hook is reported pending, no
  `finished` row was written, and the exit code is 1. It fails before the fix and passes after.
- [x] `tests/test_session_migrate_hooks.py::test_given_a_hook_refusing_its_plan_with_exit_four_when_migrating_fresh_then_nothing_moves`
  asserts that a fresh migration with the same hook still exits 2 with nothing changed.

## Lessons Learned

A preflight refusal is useful only while it still prevents a write. After a run's first
durable step, a refusal should skip only the part it guards.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
