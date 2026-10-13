---
title: "Session Migration Leaves Worktree Ownership Stale"
category: bug
tags: [bug, session-migrate, worktrees, provenance]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Session Migration Leaves Worktree Ownership Stale

## Founding Ask Coverage

**Status:** resolved

**Severity:** P1

**Created:** 2026-10-13

**Task:** tracked bug fix

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

After `ai session-migrate OLD NEW`, the home worktree and moved agent worktrees are
registered at their new paths and use their new branch names, but two ownership records
can still identify the old session and old path:

- `aih-provenance.json` retains `owner_session: OLD`.
- `branch.<branch>.aih-agent-worktree` retains the pre-migration worktree path.

Consumers of those records can therefore treat a live migrated worktree as unowned or
associate its branch with a path that no longer exists.

## Environment

The defect is platform-independent. It occurs in any repository with a registered session
worktree or agent worktree carrying either ownership record.

## Reproduction Steps

1. Create a registered session worktree and one agent worktree.
2. Give each worktree a provenance record whose `owner_session` is the old session name.
3. Set each checked-out branch's `aih-agent-worktree` configuration value to the old path.
4. Run `ai session-migrate OLD NEW`.
5. Resolve each moved worktree's provenance through `git rev-parse --git-path` and inspect
   the renamed branch's configuration.

On the unfixed implementation, both provenance records retain `OLD` and both branch
configuration values retain their old paths. A dry run also omits the planned ownership
changes.

## Root Cause Analysis

The migration had complete move and branch-rename plans, but its post-move apply phases
rewrote only session-home markers. The home-worktree path and the per-repository agent path
never inspected or updated the ownership records attached to the worktree and branch.

The causal chain was:

```text
worktree move succeeds
  -> custom ownership metadata is outside Git's built-in move bookkeeping
  -> no migration apply step rewrites that metadata
  -> readers observe the old owner session and old worktree path
```

Two competing explanations were rejected. Git's worktree move does repair its own
administrative path, but it does not understand custom provenance JSON or custom branch
configuration. Branch rename does carry the branch configuration section to the new branch
name, but it preserves the section's stale path value.

The confirmed fix is contained and reversible: it changes one migration component, adds no
shared platform abstraction, changes no public API, and requires no cross-repository runtime
dependency. The implementation profile was `implement` at medium effort because the change
touches filesystem and Git configuration boundaries but not a new architecture.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|---|---|---|
| 1 | 2026-10-13 | Relied on the existing worktree move and branch rename operations. | Rejected: both custom values remain stale after those operations. |

## Fix

Each eligible home or agent worktree move now plans its existing ownership metadata before
the first write. After that particular move succeeds, the migration:

- changes only `owner_session` when a readable provenance object names the old session;
- atomically replaces that existing record without creating one when it is absent;
- reports and skips missing or unreadable provenance records without aborting migration; and
- updates an existing `aih-agent-worktree` branch key to the moved path under the branch
  name that actually survives the rename.

Dry-run mode emits the same planned actions and performs none of the writes.

## Verification

- [x] The frozen CLI regression failed on the unfixed code because no ownership actions
  were listed and `owner_session` remained unchanged.
- [x] A real migration now rebinds both the home and agent provenance records and updates
  both branch path stamps.
- [x] Dry-run coverage verifies that provenance bytes and branch configuration are unchanged.
- [x] Missing and malformed provenance records are reported, preserved, and do not abort a
  successful worktree move.
- [x] Focused migration tests and repository lint pass.

## Lessons Learned

Moving a Git worktree updates Git's administrative state, not application-defined ownership
metadata. Every custom record keyed by worktree path, branch name, or owning session must be
part of the same explicit migration plan, including dry-run reporting and non-destructive
handling for records the migrator cannot safely read.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-10-13 | Added red-first CLI coverage for provenance and branch path stamps. | Reproduced deterministically. |
| 2026-10-13 | Added per-worktree ownership planning and post-move rebinding. | Focused regressions pass. |

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
