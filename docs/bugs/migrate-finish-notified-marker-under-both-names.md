---
title: "migrate-session --finish fails when a poller marker exists under both names"
category: bug
tags: [bug, sessions, migration, resume]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# migrate-session --finish fails when a poller marker exists under both names

## Founding Ask Coverage

`N/A`: this fix was requested through a relayed summary, not a recorded founding ask. The
ask as relayed: when a poller `.notified` marker exists under both OLD and NEW,
`ai migrate-session OLD NEW --finish` must keep the later timestamp under NEW, remove the
OLD copy and finish; `--dry-run` must name the merge; every other resume artifact found
under both names must still refuse.

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

A migration whose every other part was done could not be finished.
`ai migrate-session OLD NEW --finish` exited 1, wrote no `finished` ledger row, and its only
failed part was:

```text
resume | failed: resume artifact ~/.claude/resume/pollers/OLD__<id>.notified was NOT re-keyed: ~/.claude/resume/pollers/NEW__<id>.notified already exists
```

Each re-run printed the same line, so the finish never converged without someone deleting
one of the two markers by hand.

## Environment

Any host where a session was relaunched under its new name before its migration finished,
and a dead poller of the same attempt was announced under each name. In the measured case
both files were 11 bytes holding one epoch each; the OLD-keyed one was the later, and the
NEW-keyed one was left by the earlier relaunch.

## Reproduction Steps

1. Migrate a slot with `ai migrate-session OLD NEW -y` while one Beads store is unreadable,
   so the pair is recorded but not finished.
2. Write `~/.claude/resume/pollers/OLD__<id>.notified` and
   `~/.claude/resume/pollers/NEW__<id>.notified`, each holding one epoch.
3. Run `ai migrate-session OLD NEW --finish -y`. It exits 1 with the line above, and both
   markers stay in place.

## Root Cause Analysis

`migrate_resume_artifacts` in `src/ai_cli/session_adopt.py`, which `rekey_name_artifacts`
calls for each source key, planned `"conflict"` for every OLD-keyed artifact whose NEW-keyed
name already existed. `_plan_task_rekey` in `src/ai_cli/session_migrate.py` turned each
conflict into a `was NOT re-keyed` warning, and `summarize` in
`src/ai_cli/session_migrate_resume.py` marks a part whose warning contains `NOT ` as failed.
A failed part fails the run, so the finish exits 1 and records no `finished` row.

For most resume artifacts that refusal is right: the NEW-keyed file can be the only copy of
another session's hand-off. A poller `.notified` marker is different. Its one writer, the
supervisor that announces a dead poller, writes `f"{int(time.time())}\n"` once it has done so, and its one reader only checks that the file exists to
avoid announcing the same death twice. Two copies therefore say the same thing, and keeping
one of them loses nothing.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11-11 | Repeating `--finish` | The same conflict each time; the finish never converged |

## Fix

`migrate_resume_artifacts` plans `"merge"` instead of `"conflict"` when the source and the
existing target are both `pollers/*.notified` files and each holds one integer epoch.
`ResumeMove.kept` carries the later epoch and `describe()` prints
`pollers/OLD__<id>.notified -> NEW__<id>.notified merged (keeps the later timestamp N)`, in the
dry run and the real run alike. The real run applies the same plan: when the source holds the
later epoch it replaces the target (`os.replace`), otherwise the source is removed. Since a
merge is not a conflict, no `NOT re-keyed` warning is raised and the `resume` part reads
`would do` or `done now`.

When adoption passes several source keys, the dry run's claim tracking in
`rekey_name_artifacts` predicts a merge, and its kept epoch, for a later key whose target an
earlier key already claimed, as the real run would.

Unchanged: any other resume artifact under both names, or a marker whose content is not one
integer epoch, is still a `"conflict"` with the same warning text in migration and adoption.

## Verification

- [x] `tests/test_session_migrate_resume_markers.py::test_given_a_notified_marker_under_both_names_when_finished_then_the_later_timestamp_survives_under_new`,
  run in both orders (OLD later, NEW later), drives the reproduction above through the CLI.
  `--finish -n` names the merge and changes nothing; `--finish -y` leaves one NEW-keyed marker
  holding the later epoch, no OLD-keyed marker, `resume | done now`, a `finished` ledger row
  and exit 0. Both cases fail before the fix and pass after.
- [x] `...::test_given_a_notified_marker_and_an_outbox_file_under_both_names_when_finished_then_only_the_outbox_conflicts`
  asserts the marker merges while an outbox file under both names still fails the `resume`
  part and the run.
- [x] `...::test_given_a_notified_marker_that_holds_no_epoch_when_finished_then_it_still_conflicts`
  asserts a marker that does not parse still conflicts.
- [x] `tests/test_session_adopt.py::test_adopt_given_two_earlier_names_each_with_a_notified_marker_when_adopted_then_the_latest_epoch_is_kept`
  asserts that adoption from two earlier names keeps the latest epoch, and that its dry run
  plans the same merges and kept epochs as the real run.

## Lessons Learned

A blanket "never overwrite" rule is safe for records but blocks convergence for latches.
When every copy of a file means the same thing, merging them is decidable and the refusal
protects nothing.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
