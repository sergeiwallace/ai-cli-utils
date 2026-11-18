---
title: "Session Migration Leaves a Stored Venv's Back-Pointer Naming the Old Checkout"
category: bug
tags: [bug, session-migrate, venv, worktrees]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Session Migration Leaves a Stored Venv's Back-Pointer Naming the Old Checkout

## Founding Ask Coverage

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-13

**Task:** tracked bug fix

Ask: session migrations should succeed completely, so that no artifact keeps the old name or
path afterwards and no migration has to be redone.

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)
- [Fix Log](#fix-log)

## Symptoms

Some environments keep each checkout's virtual environment in a shared store outside the
checkout: the checkout's `.venv` is a symlink to `<store>/<repo>--<leaf>/`, and that store
directory holds `.<prefix>-checkout`, a one-line file with the absolute path of the checkout
that owns it. `<prefix>` is the same `[worktree] marker_prefix` namespace as the
`<prefix>-provenance.json` record.

After `ai migrate-session OLD NEW`, the slot and its agent worktrees sit at their new paths
and `.venv` still links to the same store directory, but the record still names the old path.
Tooling that checks the record against the checkout then refuses that checkout. On one host,
123 of 140 records named a path that no longer existed. About 93 of them named pre-migration
agent containers (`<repo>/.worktrees/OLD-agents/<leaf>`).

## Environment

The defect does not depend on the platform. It occurs wherever a checkout's `.venv` links into
a store whose directory carries the back-pointer record, and `[worktree] marker_prefix` is set.

## Reproduction Steps

1. Create a slot `.worktrees/OLD` and an agent worktree `.worktrees/OLD-agents/leaf-a`.
2. For each one, create a store venv holding `.<prefix>-checkout` with that checkout's path,
   and link the checkout's `.venv` to it.
3. Run `ai migrate-session OLD NEW -y`.
4. Read both records: each still names the `OLD` path.

For a pair migrated before this fix, `ai migrate-session OLD NEW --finish -n` lists nothing for
the record, and the pair reads as verified finished.

## Root Cause Analysis

Causal chain:

- **Trigger.** A migration moves a checkout whose `.venv` is a symlink into the store.
- **Incorrect decision.** The planners (`_start_session` for the slot,
  `session_migrate_agents.plan_agents` for agent worktrees, `session_migrate_resume.plan_home`
  for `--finish`) consider only the files inside the venv (`venv_relocate._candidates`:
  `pyvenv.cfg`, scripts, `.pth`, egg-links, editable finders, `direct_url.json`). The store
  record lives next to those files but is not one of them, so no planner reads it.
- **Propagation.** The symlink is absolute, so it survives the move and the venv keeps working.
  Nothing during the migration fails, and the re-plan that judges a pair finished cannot see the
  record either, so the pair is recorded `verified`.
- **Symptom.** The record names a path that no longer exists, and the store's owner refuses it.

Hypothesis ledger:

| Hypothesis | Predicted observation | Check performed | Result |
|---|---|---|---|
| H1: no planner reads the record | the dry run has no row for it; the record keeps the old path after a migration | the RED tests below, source read of the three planners | Confirmed |
| H2: the migration breaks the `.venv` link, so the record is out of reach | moved checkouts' `.venv` is dangling or points elsewhere | resolved `.venv` for the 50 stale records whose leaf still exists under `NEW-agents/` | Rejected: all 50 resolve to the store directory holding their own record |
| H3: the stale records are all from moved checkouts | every stale record's leaf exists under a new container | searched each repo's `.worktrees/*-agents/<leaf>` | Partly rejected: about 50 leaves exist; the rest were reaped after migration and have no current path to name |

The store location needs no configuration: a migration reaches the record through the checkout's
own `.venv` link, so the fix works for any store path.

## Prior Fix Attempts

None. The venv-relocation fix that rewrites editable finders and egg-links covered the files
inside the venv, not the store's record.

## Fix

- `venv_relocate.stale_checkout_record(venv, old_root)` reads `<venv resolved>/.<prefix>-checkout`
  and returns it only when its content, less a trailing newline, is exactly one spelling of
  `old_root`. A record naming anything else, a path that merely starts with `old_root`
  included, is never returned. It returns nothing when `marker_prefix` is unset.
- `venv_relocate.repoint_checkout_record` re-checks the content, then writes the new path with
  the same trailing newline and mode to a temporary file in the same directory and renames it
  over the record.
- The record is a planned part, `venv back-pointer`, in the slot migration, in each moving or
  already-moved agent worktree's plan, and in the `--finish` home plan. A dry run lists it, and
  the re-plan counts it as left until the record names the new path. An agent worktree that has
  already moved, with nothing else left to carry, is still planned when its record is stale.

Scope decision (Step 2.5): one component (the migration planners and the venv module), no public
contract change, no new boundary. A contained fix.

Not covered: a record naming a worktree that was reaped after its migration. It has no current
checkout to point at; the store's owner reconciles those.

## Verification

- RED on unfixed code: both tests below failed for the expected reason (the record still named
  `myrepo-3`; the `--finish` dry run had no `venv back-pointer` row).
- GREEN after the fix, plus the migrate-session test files and the whole repository suite,
  `ruff check` and `ruff format --check`.

Regression tests in `tests/test_session_migrate_agents.py`:

- `test_given_venv_back_pointers_naming_moved_checkouts_when_migrated_then_they_name_the_new_paths`
- `test_given_a_verified_pair_whose_venv_back_pointers_name_old_paths_when_finished_then_they_are_repointed`

Both also assert that a record naming an unrelated checkout, and one naming a sibling path that
extends the moved worktree's, stay byte-identical.

## Lessons Learned

The venv-relocation fix asked "which files inside the venv name the checkout?", not "which
files anywhere name the checkout?". The store record sits beside the venv's files, written by
other tooling, so a scan of the venv's own layout missed it. Reading the checkout's own links
(`.venv`) for records that name it finds this kind of artifact without any store configuration.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-11-13 | Plan and rewrite the store record as the `venv back-pointer` part in migrate and `--finish` | RED tests now pass |

<!-- /doc:region name="summary" -->
