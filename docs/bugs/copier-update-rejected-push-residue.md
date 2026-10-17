---
title: "Copier Update Rejected Push Residue"
category: bug
tags: [bug, copier, git, worktree, recovery]
status: fix-implemented
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Copier Update Rejected Push Residue

## Founding Ask Coverage

**Status:** fix-implemented

**Severity:** P1

**Created:** 2026-10-17

**Task:** tracked bug fix; tracker commands were explicitly out of scope for this run

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

When an isolated `ai copier-update` created its update commit but `git push origin HEAD:main`
was rejected, the command printed that the commit was ready for a manual push. It left the
`copier-update` linked worktree and `copier-update-tmp` branch in the target repository, without
creating a recovery issue. Repeated fleet updates therefore accumulated source-control residue.

## Environment

The defect affected isolated updates with push enabled. It did not affect successful pushes,
no-change runs, or runs with push disabled. The regression fixture uses local Git repositories and
a bare origin; it advances the origin after the updater's fetch so the real main-branch push is
rejected as non-fast-forward while a backup-ref push remains possible.

## Reproduction Steps

1. Create a Copier-managed project with a local bare origin.
2. Start an isolated update from the fetched `origin/main` tip.
3. Advance the origin's `main` branch from a competing clone before the updater pushes.
4. Let the updater commit and run its real `git push origin HEAD:main` command.

On the unfixed code,
`test_given_origin_rejects_update_when_entry_runs_then_anchor_is_pushed_and_temp_state_is_removed`
failed because `.worktrees/copier-update` still existed. The captured command output said the
commit was ready in the temporary worktree and should be pushed manually.

## Root Cause Analysis

The causal chain was:

```text
main-branch push returns nonzero
  -> _do_update_in_worktree returns pushfail with Git's error output
  -> _update_one_isolated verifies that the clean temporary branch is ahead of its base
  -> an early return bypasses the existing anchor, issue, and cleanup block
  -> the worktree and temporary branch remain, with no durable remote recovery ref or issue
```

The hypothesis that cleanup itself failed was rejected: the unfixed path never called cleanup for
a clean rejected push. The hypothesis that only hook-modified dirty worktrees were affected was
also rejected: the real-Git reproduction left a clean committed checkout and still retained it.

The fix remained bug-sized. It changes the existing isolated-update recovery component, introduces
no public API, and crosses no repository or architectural boundary. The implementation role was
`implement` at medium effort because the cause was established and the Git boundary required a
real integration test, but no redesign.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-10-17 | Preserve every rejected push in its temporary worktree for manual recovery. | Safe for the commit, but accumulated worktrees and provided no issue-backed fleet recovery path. |
| 2 | 2026-10-17 | Attribute retained worktrees to cleanup failure. | Rejected: the clean-push branch returned before recovery or cleanup was attempted. |

## Fix

Rejected pushes now follow the verified recovery path:

1. Snapshot the failed update under the dated `refs/backup/.../copier-update/...` namespace.
2. Push that exact backup ref to `origin` without bypassing hooks.
3. Read the remote ref back and require it to match the local anchor.
4. File or refresh the repository recovery issue with the anchor, template range, and original push
   diagnostics.
5. Confirm the linked worktree and saved `HEAD`, then remove only the temporary worktree and branch.
6. Include the anchor ref in the project's push-failure summary.

If local anchor creation, anchor push, or remote verification fails, cleanup is not attempted and
the result is `recoveryfail`, preserving the temporary worktree and branch.

## Verification

- [x] RED before the production edit: the real non-fast-forward push regression failed because the
  temporary worktree remained and the command requested a manual push.
- [x] A second RED case showed that the unfixed path never attempted an anchor push.
- [x] `uv run pytest -q tests/test_copier_update_recovery.py` — 11 passed.
- [x] `uv run pytest -q tests/test_copier_update.py tests/test_copier_update_recovery.py
  tests/test_copier_update_memory.py tests/test_worktree_removal_guards.py` — 109 passed.
- [x] The success regression proves the temporary worktree and branch are gone, the local and
  remote anchor values match, the issue filer receives the anchor and push diagnostics, and the
  summary line names the anchor.
- [x] The failure regression proves an anchor-push refusal retains the worktree and branch, does not
  file an issue claiming remote recovery, and reports `RECOVERY FAILED`.
- [x] `uv run ruff check .` and `uv run ruff format --check .` — clean.
- [ ] `uv run pytest -q -n 8` cannot complete in the implementation sandbox. The suite's shared
  loopback-server fixture fails before test execution because creating an IPv4 stream socket raises
  `PermissionError: [Errno 1] Operation not permitted`; setting the child-process local-binding
  hint does not change the sandbox policy. The required full gate must be rerun in the normal test
  environment with loopback binding enabled.

## Lessons Learned

A locally preserved commit is not sufficient recovery for unattended fleet automation. Recovery is
durable only after an independently named ref is verified on the remote and the failure is filed in
the repository's normal work queue. Cleanup must remain strictly after that proof, and its failure
path needs direct coverage so a future simplification cannot trade residue reduction for data loss.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-10-17 | Added real-Git rejected-main-push and failed-anchor-push regressions. | RED on the retained-worktree behavior. |
| 2026-10-17 | Routed rejected pushes through verified local/remote anchors, issue filing, and guarded cleanup. | Focused and nearby suites pass; full gate is permission-blocked as documented above. |

<!-- /doc:region name="summary" -->
