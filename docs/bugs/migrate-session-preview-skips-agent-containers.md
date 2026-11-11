---
title: "A migration preview does not show what happens to the agent containers"
category: bug
tags: [bug, sessions, migration]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A migration preview does not show what happens to the agent containers

## Founding Ask Coverage

`N/A`: this fix was requested through a relayed summary, not a recorded founding ask. The
ask as relayed: `ai migrate-session OLD NEW -n` must name the fate of each `OLD-agents`
container that the real run sweeps, and must remove nothing.

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

For every `<repo>/.worktrees/OLD-agents` container, `ai migrate-session OLD NEW -n` printed
`agent container: <path> (removed once empty)` and exited 0. The real run then decided each
container's fate itself. A container holding a stray file came out as
`agent container: <path> (kept: holds notes.txt)`, and the real run exited 1. So the preview
passed while the migration it described left work undone.

## Environment

Any host. The sweep runs in the slot's repo and in every repo under `[project] projects_dir`.

## Reproduction Steps

1. Create a registered slot `.worktrees/OLD` and a directory `.worktrees/OLD-agents` holding
   `notes.txt`.
2. Run `ai migrate-session OLD NEW -n`. It prints `(removed once empty)` and exits 0.
3. Run `ai migrate-session OLD NEW -y`. It prints `(kept: holds notes.txt)` and exits 1.

## Root Cause Analysis

`_clean_other_repos` in `src/ai_cli/session_migrate.py` is the step that reports the
containers' fate. It called `_sweep_containers` only when the run was not a dry run:

```python
if not result.dry_run:
    result.moves += _sweep_containers(containers, live_cwds, dry_run=False)
```

`_sweep_containers` already had a `dry_run` mode that removes nothing, but nothing called it.
The only container line in the preview came from the agent plan (`_agents.describe`), and
that line is the same for every container: `removed once empty`. It never looks at what the
container holds or who is working in it.

A preview call alone is not enough, though. During a dry run the container still holds the
agent worktrees that the real run moves to `NEW-agents` before the sweep. Without that
information the preview would report a container as kept when the real run will remove it.

## Prior Fix Attempts

None.

## Fix

`_clean_other_repos` now takes the agent plan and calls `_sweep_containers` in both modes.
In a dry run it passes the set of agent worktrees the plan moves out (`moving`), and
`_holds_a_file` treats each of them as gone. The preview then prints the line the real run
prints: `would be removed: empty`, or `kept` with what the container holds or the pid
working in it. A kept container makes the preview exit 1, as the real run does. The real
run's sweep is unchanged.

## Verification

- [x] `tests/test_session_migrate.py` drives `ai migrate-session` through the CLI on fixture
  slots:
  - `test_given_an_empty_old_agent_container_when_dry_run_then_its_removal_is_named_and_it_stays`
  - `test_given_an_old_agent_container_holding_a_moving_agent_worktree_when_dry_run_then_its_removal_is_named`
  - `test_given_an_old_agent_container_holding_a_file_when_migrated_or_dry_run_then_both_name_it_kept_and_exit_1`,
    for both `-n` and `-y`

  The three dry-run cases fail before the fix and pass after it. The moving-worktree test also
  fails when the dry-run sweep is called without the `moving` set.

## Lessons Learned

When a preview step skips a call the real run makes, the preview cannot show that call's
result. A preview also has to account for the earlier steps it does not perform, or it will
report the state before those steps rather than the state the real run reaches.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
