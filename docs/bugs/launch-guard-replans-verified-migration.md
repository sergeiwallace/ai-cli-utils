---
title: "ai c NEW re-plans a verified migration, sits silent for minutes, then refuses"
category: bug
tags: [bug, sessions, migration, launch, observability]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# ai c NEW re-plans a verified migration, sits silent for minutes, then refuses

## Founding Ask Coverage

The ask: session migrations must succeed completely and never need running again, every
artifact must use the new name afterwards, and a long-running step must never sit silent on
one log line. This fix covers the launch side of that ask: a migration already proved finished
no longer blocks its own session, a session that is already running is never made unreachable,
and every slow step of a finish or re-plan prints when it starts and when it ends.

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-12

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

`ai c NAME`, for a session whose migration OLD -> NAME had finished, printed
`session: resolved NAME` and then nothing for 34 seconds to over 5 minutes. It then refused:

```text
refusing to launch NAME: the migration OLD -> NAME is partial; parts left:
migration (failed: could not re-plan: could not list registered git worktrees for
/path/to/other-repo: (no stderr output)); finish it with `ai migrate-session OLD NAME --finish -r REPO`
```

The ledger's latest row for the pair was `{"finished": true, "verified": true, ...}`: an
empty re-plan had already proved the migration finished.

A second case: NAME was running in tmux and its pair had a legacy `finished` row without
`verified`. The re-plan found a part left, so `ai c NAME` refused, and
`ai migrate-session OLD NAME --finish` refused because NAME was live. The running session could
be neither attached to nor finished.

## Environment

Any host where the launch guard re-plans a recorded pair and some host-wide read fails or is
slow: a Beads store in another repo that `bd` cannot open, an external hook whose `--dry-run`
is slow, or `git worktree list` exiting non-zero in another repo. A measured re-plan took 3
minutes: 7 sequential `bd --sandbox list` calls, one per store, about 10 s each (69 s); one
external hook dry run, 52 s; and about 40 s of git calls. A pending pair runs a writing finish
and then the verify re-plan, so it takes about twice that.

## Reproduction Steps

1. Migrate a slot OLD -> NEW and finish it, so the ledger's latest row for the pair is
   `finished` with `verified: true`.
2. Make a host-wide read fail: a Beads store under the projects directory whose `bd list`
   exits non-zero, or a hook added to `migrate-session.d/` after the finish.
3. Run `ai c NEW`. It prints nothing between `session: resolved NEW` and a refusal naming the
   failed store or hook, and exits 1.

## Root Cause Analysis

1. `_finish_recorded_migrations` in `src/ai_cli/main.py` called `replan(...)`, a full
   read-only `finish_session(dry_run=True)`, for every recorded pair that was not pending,
   whatever its ledger row said. A verified row was re-proved at every launch. The re-plan
   reads state the pair does not own: every Beads store, every hook's `--dry-run`, and
   `git worktree list` in every repo. Any transient or unrelated failure there became a part
   left, and a part left refuses the launch. `session_migrate_status.finish_state` already
   returned `verified` for such a pair, and `ai migrate-session --status` already skipped it.
   The launch guard did not ask.
2. Nothing printed while `finish_session` ran its roughly 70 subprocesses. The launch
   reporter's phases print at start and end, but the guard opened none, and `finish_session`
   had no way to report a step.
3. `registered_worktrees` in `src/ai_cli/session.py` raised with git's stderr only. When git
   exited non-zero with empty stderr, the message read `(no stderr output)` with no exit
   status, so the failure could not be diagnosed.
4. The guard ran before the launch knew whether it would attach to a running session. A
   writing finish refuses a live session, so for a running NEW the guard could only re-plan,
   and a part left refused the attach while `--finish` refused the finish.

Rejected hypotheses:

- The verified row was not being written. Rejected: the measured ledger had
  `"verified": true`, and `finish_state` read it as `verified`.
- `git worktree list` was hanging. Rejected: it exited non-zero quickly; the minutes were the
  sum of the Beads, hook and git reads across the host.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11 | AI-CLI-ofp7 D-2: re-plan every pair at its own launch, so a dirtied verified pair is caught | Made launch availability depend on every store and hook on the host; this doc revises it |

## Fix

- The launch guard reads `finish_state` first. A pair whose latest row is a verified
  `finished` is trusted: it is neither re-planned nor finished at launch, and the launch prints
  `migration OLD -> NEW: verified finished earlier, not re-planned`. `ai migrate-session
  --status -a` and `--finish` remain the way to re-check it. Pending pairs are still finished at
  launch, and `finished` rows without `verified` are still re-planned. The revision is recorded
  next to D-2 in `docs/plans/ai-cli-ofp7-migrate-session-completeness-plan.md`.
- A launch that will attach to NEW's live tmux session (not bare, not `--sandbox`, not a recipe
  replay) re-plans each pair that is not verified, read-only, and turns a part left into one
  warning naming the `--finish` command to run once NEW exits. It then attaches. The live-pane
  check is the same `_tmux_session_has_live_pane` the worktree sync uses, and it runs only when
  a pair that is not verified exists.
- `finish_session`, `migrate_session` (and `_start_session`), `rekey_beads`, `clean_leftovers`,
  `session_migrate_hooks.plan_hooks` and `run_hooks`, the Beads read, re-key and publish loop,
  and `session_migrate_status.replan`, `replan_live` and `verify` take an optional
  `progress: Callable[[str], None]`. Each slow step prints `<what>: <doing>` as it starts and
  `<what>: <outcome> (<elapsed>s)` as it ends, through `session_migrate_progress.timed`. The
  launch guard passes the launch reporter at default verbosity, and `ai migrate-session`
  (single pair, `--finish`, `--beads-only`, `--status`, `--finish -a`, `-a -T`) passes its own
  stderr output. `None`, the default, prints nothing.
- `registered_worktrees` names the exit status when stderr is empty:
  `could not list registered git worktrees for X: exit 128 (no stderr output)`, or
  `killed by signal 9 (SIGKILL) (no stderr output)` for a signalled git.

## Verification

The RED runs ran the new tests against the base commit's `src/`, extracted with `git archive`
and put first with `-o pythonpath=<that src> .`. `PYTHONPATH` alone is not enough, because
`pyproject.toml` puts the checkout's own `src` first. The GREEN runs used the fixed tree.

- [x] `tests/test_session_migrate_on_launch.py`, 13 new or revised tests. RED: 11 failed, 2
  passed. The two passes are the stopped-NEW refusal tests, which guard behaviour that did not
  change. The RED failure reasons were:
  - `..._verified_finish_and_a_host_a_re_plan_would_call_partial_..._launches_unplanned[_store_unreadable]`
    refused with `parts left: beads store (failed: ...)`.
  - `[_hook_added_later]` refused with `hook notes (would do)`.
  - `[_hook_refusing_added_later]` refused with the hook's preflight failure.
  - `..._the_finish_is_verified_..._trusted_unplanned_and_launches`: the hook ran its dry run
    again at launch.
  - `..._verified_finish_dirtied_later_..._status_all_still_finds_it`: the launch was refused.
  - `..._new_running_and_a_part_left_..._attaches_with_one_warning[pending]` was refused with
    `tmux session c-NEW still hosts NEW`. `[unverified]` exited 1.
  - `..._unverified_finished_row_..._re_planned_and_refused`, `..._pending_pair_..._each_slow_step_prints_...`
    and `..._migrate_session_finishes_it_then_each_slow_step_prints_timed` failed because no
    progress line was printed.
  - `..._the_launch_help_...` failed because the help still said a finished pair is re-planned.

  GREEN: the whole file and `TestCreateWorktree`, 37 passed.
- [x] `tests/test_session.py::TestCreateWorktree::test_given_git_fails_with_no_stderr_when_worktrees_are_listed_then_the_error_names_the_exit_status`
  uses a fake `git` on PATH that exits 128 with no stderr, or kills itself with SIGKILL. RED:
  both cases read only `(no stderr output)`. GREEN: `exit 128 (no stderr output)` and
  `killed by signal 9 (SIGKILL) (no stderr output)`.

On the host that ran these tests, one `git --version` took 2.46 s of wall time and no
measurable CPU time. Each migration test makes dozens of git calls, so each one takes minutes
there.

## Lessons Learned

A row written as proof should be read as proof. Re-deriving a verdict on every use makes the
consumer depend on everything the derivation reads, and here that was the whole host. A guard
on a launch also has to know what the launch is about to do: a refusal that cannot be acted on,
because the remedy refuses the same live session, only locks the user out.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
