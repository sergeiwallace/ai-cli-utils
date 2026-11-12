---
title: "ai sessions list kept rows for sessions that were gone, renamed or retired"
category: bug
tags: [bug, sessions, registry, retention]
status: fixed
template_version: "bug-1.0.0"
---

# ai sessions list kept rows for sessions that were gone, renamed or retired

## Summary

<!-- doc:region name="summary" kind="replaceable" -->

`ai sessions list` showed rows that no longer described any session: exited and
retired sessions, one-off probe sessions, legacy rows from earlier boots, and on a
client machine, rows for remote sessions that had since been renamed on their own
host and read `unknown` forever. Nothing in the code ever removed a run-state row.

<!-- /doc:region name="summary" -->

## Reproduction

<!-- doc:region name="reproduction" kind="replaceable" -->

On a Linux host with about 20 rows, about half were exited; the oldest exited row
was three weeks old, and several belonged to sessions `ai retire-session` had
retired days earlier (each name appears in `session-retirements.jsonl`). A legacy
row from an earlier boot with no `owning_host` read `unknown` for a month. The
regression tests in `tests/test_session_stale_rows.py` and
`tests/test_session_retire.py` rebuild each class in a temp registry with fake tmux,
process table, boot time and remote probe, and drive `cli()`.

<!-- /doc:region name="reproduction" -->

## Root Cause

<!-- doc:region name="root_cause" kind="replaceable" -->

A row is written by every launch (`record_launch`, `session_run_state.register`,
`begin`) and changed by `mark`, reconcile, migrate-session and restore. Before this
fix, only three paths could remove one, and none reached these rows:

- `session_registry.remove()` and `prune()` keep any row with a boot recipe
  (`or r.get("boot_recipe")`, `and not record.get("boot_recipe")`), which every
  owning-host row has since boot relaunch landed.
- `prune()` runs only from `ai iterm2 sessions -p`; nothing schedules it.
- Restore removes only rows whose relaunch scenario is off, on macOS.

Reconcile settles a row as `exited` but never removes it, and by design skips rows
from an earlier boot, rows with another or no owning host, and remote rows
(`record.get("kind") != "local"`, `record.get("boot_id") != boot`). So:

- Exited, retired and probe rows stayed forever. `ai retire-session` dropped the
  slot's canonical-registry entry but never touched the run-state registry.
- Rows from an earlier boot stayed `unknown` forever.
- A client-side remote row stores the remote tmux name it dialled.
  `migrate-session` renames rows only in the registry of the host it runs on
  (`_apply_iterm2`), and nothing ever re-checks a remote row, so the client's row
  kept the old name with no run state, which reads `unknown`. A row launched over
  mosh never records its clean exit either, so only a probe can settle it.

<!-- /doc:region name="root_cause" -->

## Scope of Fix

<!-- doc:region name="scope_of_fix" kind="replaceable" -->

The data layer only: which rows exist. Output formatting is unchanged. Probe-mode
launches that write no row at all are a separate change; the age-out below clears
probe rows already written, whichever version wrote them.

<!-- /doc:region name="scope_of_fix" -->

## Fix

<!-- doc:region name="fix" kind="replaceable" -->

`session_reconcile.age_out()` runs after reconcile in `ai sessions list` and in
the minute worker. It removes a local row only when tmux and the process table
both prove its session gone, no deny-boot safety file exists, and the row has not
changed for `[session_registry] stale_row_retention_hours` (default 72, read on
every call, 0 turns it off). A row from an earlier boot counts from this boot's
start, so boot relaunch keeps its window. The minute worker alone also handles
remote rows past the retention whose launcher is gone: a recorded clean exit
removes it, otherwise one ssh `tmux has-session` removes it only on proved
absence, and an unsettled row is stamped `probed_at` so it waits another
retention. `ai sessions list` never dials a host: an unreachable host could hold
an interactive list for up to 20 s. Removal skips any row that changed after the
pass read it.

`ai retire-session NAME` now drops this machine's local rows for NAME (every
engine and `-r` variant) after the checkout is removed, and its plan and dry run
name the count.

The rule is documented in `docs/iterm2-persistence.md`, "When a row leaves the
registry".

<!-- /doc:region name="fix" -->

## Verification

<!-- doc:region name="verification" kind="replaceable" -->

The new tests failed on the base commit (11 failed) and pass with the fix. The
keep cases (live tmux, unprovable tmux, a live agent process, a deny-boot safety
file, a recent boot, a live launcher, a refused retirement) pass on both, and
removing the liveness check makes three of them fail. On a copy of the host
registry above, the fixed `ai sessions list` removed the three rows past the
retention whose sessions were gone and kept every live row.

<!-- /doc:region name="verification" -->

## Lessons Learned

<!-- doc:region name="lessons_learned" kind="replaceable" -->

A guard added for one consumer (keep a boot recipe for relaunch) silently became
"keep forever" for every other one. A registry that records state needs an
explicit exit path as well as an entry path.

<!-- /doc:region name="lessons_learned" -->

## Fix Log

<!-- doc:region name="fix_log" kind="append_only" -->

- 2026-11-12: age-out in `ai sessions list` and the minute worker; retire drops rows.

<!-- /doc:region name="fix_log" -->
