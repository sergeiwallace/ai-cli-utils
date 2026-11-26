---
title: "Session Migration Leaves Links Naming the Old Agents Directory"
category: bug
tags: [bug, session-migrate, venv, worktrees, symlinks]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Session Migration Leaves Links Naming the Old Agents Directory

## Founding Ask Coverage

**Status:** resolved

**Severity:** P2

**Created:** 2026-11-16

**Task:** tracked bug fix

Ask: after `ai migrate-session OLD NEW`, every venv link in the session's agent worktrees
resolves, and a finish that leaves a link naming an old-name path fails and says which one.

## Table of Contents

- [Symptoms](#symptoms)
- [Root Cause Analysis](#root-cause-analysis)
- [Fix](#fix)
- [Verification](#verification)
- [Not Covered](#not-covered)

## Symptoms

After a migration renamed `.worktrees/OLD-agents` to `.worktrees/NEW-agents`, a venv check
failed on eight agent worktrees with `FileNotFoundError` naming the pre-migration agents
directory, and the venv tooling's own repair refused on all eight with the same error. Each
worktree's `.venv` resolved, directly or through a venv slot link, to a path under
`OLD-agents/`, which no longer exists.

## Root Cause Analysis

No migration planner reads a symlink. The planners carry a venv's install records
(`venv_relocate._candidates`) and its back-pointer record (`stale_checkout_record`), both
files reached *through* `.venv`, but never the link itself or any other link in the checkout:

- `src/ai_cli/session_migrate_agents.py` `plan_agents`, the `if entry.moves:` block, plans only
  `needs_relocation` and `stale_checkout_record` for a moved worktree;
- `src/ai_cli/session_migrate.py` `_start_session` does the same for the slot (`has_venv`,
  `slot_record`);
- `src/ai_cli/session_migrate_resume.py` `plan_home` does the same for `--finish`.

`git worktree move` moves the directory with its links unchanged. An absolute link naming
`OLD-agents/...`, or a relative one climbing out to it by name, then dangles. Because no
planner can see it, the re-plan that judges a pair finished cannot see it either, and the pair
is recorded `verified`.

Hypothesis ledger:

| Hypothesis | Predicted observation | Check performed | Result |
|---|---|---|---|
| H1: no planner reads links | a migrated worktree's link into `OLD-agents/` keeps its target | RED test (a) below, source read of the three planners | Confirmed |
| H2: links into a shared venv store break | `.venv -> <store>/<slot>` dangles after a migration | read every agent worktree `.venv` link on one host (354 links) | Rejected: all resolve; a store link names no checkout path |
| H3: the link that dangles is outside the checkout | the `.venv` link is fine, the store's slot link names `OLD-agents/` | RED test (b) below | Reproduced as a case the migration must report, since it never writes outside the session's worktrees |

## Fix

`src/ai_cli/session_migrate_links.py`:

- `scan` walks each worktree without following a link, entering `.git` or a venv, and reads
  every link. A link whose target, judged from where it will be after the move, lies in a
  renamed root (the slot or an `OLD-agents`/`<host>--OLD-agents` container) is planned as a
  `stale link` part, rewritten to the same path under NEW; a relative link stays relative.
- A link whose target is outside the worktrees is followed read-only through each outside
  link. One that names an old-name path cannot be fixed here and is planned as a skipped
  `stale link`, so the re-plan counts it as left. So is a Windows junction naming one.
- After every writing run and `--finish`, `check` re-scans the moved worktrees and adds a
  `stale link NOT fixed` warning per link still naming an old-name path, so the run exits 1
  with the list.

## Verification

Regression tests in `tests/test_session_migrate_agents.py`, at the `ai migrate-session` entry
point:

- `test_given_agent_worktrees_with_links_into_the_old_agents_dir_when_migrated_then_every_link_names_the_new_path`
- `test_given_a_venv_link_resolving_through_an_outside_link_into_the_old_agents_dir_when_migrated_then_it_fails_closed`
- `test_given_links_pointing_outside_the_sessions_worktrees_when_migrated_then_they_are_left_untouched`

The first two failed on unfixed code (a link still named `OLD-agents/`; the run exited 0). The
third passed before and after: it guards that the fix leaves outside links alone.

## Not Covered

A link outside the session's worktrees is reported, never rewritten, a venv store's own slot
link included. Its owner repairs it, then `ai migrate-session OLD NEW --finish` verifies the
pair.

<!-- /doc:region name="summary" -->
