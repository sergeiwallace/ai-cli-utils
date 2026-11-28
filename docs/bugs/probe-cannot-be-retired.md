---
title: "[AI-CLI-s8c6f] An exited probe could not be retired: retire-session looked only in .worktrees/"
category: bugs
tags: [bug, session, probe, retire-session, worktree]
status: fix-deployed
severity: P2
task: AI-CLI-s8c6f
---

# [AI-CLI-s8c6f] An exited `ai c -t` probe could not be retired

**Status:** fix-deployed

**Severity:** P2

**Created:** 2026-11-22

**Task:** `AI-CLI-s8c6f`

## Symptoms

`ai c -t` says a probe can be removed like any scratch worktree once it exits. Four exited
probes under `<state>/probe-worktrees/<repo>-<digest>/<title>` (branches `probe-<title>`,
no commits, clean) could not be removed with the documented command:

```text
$ ai retire-session myrepo-host-a-probe-1 -y
Error: <repo>/.worktrees/myrepo-host-a-probe-1 is not a registered worktree
```

A worktree-reap audit outside this repository also classified each one as a canonical
session home by its name and pointed to `ai retire-session`, so neither route removed one.

## Root cause

`retire_session` in `src/ai_cli/session_retire.py` built the worktree path from the name,
`<repo>/.worktrees/<name>`, and refused when nothing was registered there. A probe's
worktree lives under `probe_worktree_base` (`src/ai_cli/session.py`), outside the
repository, by design. The session's run-state record knows where it is (`cwd` and the boot
recipe's `cwd`), but retirement never read it.

A sibling instance of the same class: `_print_launch_plan` in `src/ai_cli/main.py`, the
`-d/--dry-run` report, also built `<repo>/.worktrees/<title>` and `wt-<title>` from the name.
`ai c -t -d` therefore planned a worktree and branch the real probe launch never uses.

## Fix

- `session_worktree(repo_root, name, registered)` in `src/ai_cli/session_retire.py` reads
  the worktree from the run-state records of `name` (any engine's tmux name, `c-NAME` and
  the others). A recorded directory counts only when it is a linked worktree of the
  repository. The main checkout, which a `-W/--no-worktree` session records, never counts.
  With no such record it uses `.worktrees/NAME`. A record and the slot naming different
  worktrees, or two records naming two, refuse with both paths named. `retire_session`
  calls it, so an exited probe is retired with the same liveness and content checks as a
  session home, and its `probe-<title>` tip is anchored.
- `session_worktree_slot(repo_root, ai_name, probe=)` in `src/ai_cli/session.py` returns the
  directory and branch a launch uses. `create_worktree` and the dry-run plan both call it,
  so the plan names the probe directory and the `probe-<title>` branch.
- The launch help, `ai retire-session` help, README, `docs/tools/cc-session-adoption.md`
  and `docs/designs/probe-sessions.md` now say that `ai retire-session` removes a probe.

## Class search

Every place that builds an existing session's worktree path from its name.

- Query: `grep -rnE "WORKTREE_DIR\s*/|\"\.worktrees\"\s*/|'\.worktrees'\s*/|/ \"\.worktrees\"|/ WORKTREE_DIR" src/ai_cli`.
- Haystack: every `.py` file under `src/ai_cli/` at base `cec73193` (`git grep` there): 51 hits
  in 12 files.
- Fixed: `session_retire.py` `retire_session` (the reported instance) and
  `main.py` `_print_launch_plan` (the dry-run plan). `session.py` `create_worktree` now
  shares the slot function with the plan.
- Correct as they are, because the operation applies only to a canonical session home and a
  probe is never one:
  - `session_migrate.py`, `session_migrate_resume.py`, `session_migrate_status.py`,
    `session_migrate_agents.py` and `main.py`'s migration helpers (`_migration_target_is_occupied`,
    the finish re-plan, `_auto_migrate_legacy_launch`): renaming a session home. The migrate
    route's own retirement goes through `retire_session`, so it reads the record too.
  - `session_adopt.py` and `migrate_names.py`: adopting or naming a session into a home slot.
  - `main.py` `_chief_has_saved_session`: the chief's own home.
  - `main.py` `_resolve_session_identity` and the legacy-title validation at launch: whether
    a retired or legacy home exists for a name. A probe is named by index and skips both.
  - `session.py` slot-index and `.envrc` approval code: already reads both `.worktrees/` and
    the probe directory.
  - `session_locate.py` `unregistered_sessions`: sessions named before the name registry.
    A probe holds no name and is never one.
- Unrelated: `sync.py` and `copier_update.py` build their own tool worktrees by fixed name.

## Reproduction and tests

Real entry points: the `ai retire-session` and `ai c -t -d` command lines through `cli()`.

- `tests/test_probe_launch.py`
  `test_given_an_exited_probe_when_retired_by_name_then_its_worktree_is_removed_and_its_tip_anchored`:
  a real `ai c -t` launch, then `ai retire-session <title> -y`. RED on `cec73193` with the
  error above. Green after the fix.
- `tests/test_probe_launch.py`
  `test_given_a_probe_launch_when_dry_run_then_the_plan_names_its_worktree_outside_the_repository`:
  RED on `cec73193` (the plan named `.worktrees/<title>` and `wt-<title>`). Green after.
- `tests/test_session_retire.py`
  `test_given_a_record_naming_the_repository_root_when_retiring_a_name_with_no_slot_then_it_is_refused`
  and `test_given_a_record_naming_another_worktree_than_the_slot_when_retiring_then_it_is_refused_naming_both`:
  constraints on the fix, so a record never makes retirement remove the main checkout or
  pick one of two worktrees. Both pass before and after.
- `tests/test_probe_launch.py`
  `test_given_an_exited_probe_whose_record_was_pruned_when_retired_by_name_then_its_worktree_is_removed`:
  the fix above read only the run-state record, and `ai iterm2-sessions --prune` drops an
  exited probe's record by design, so a probe retired after the next prune was refused again
  with `no session record names one`. Measured live on four exited probes after the fix was
  installed. `session_worktree` now also checks the probe directory for the name, beside the
  `.worktrees/<name>` slot.

## Outside this repository

The worktree-reap audit that called a probe canonical keys on the directory name alone. It
needs to recognise the probe directory root (`<state>/probe-worktrees/`) and the
`probe-` branch prefix as scratch, not as a session home.
