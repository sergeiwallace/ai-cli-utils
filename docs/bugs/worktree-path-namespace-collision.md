---
title: "[AI-CLI-200] .worktrees/<name> means two things, and one of them deletes work"
category: bugs
tags: [bug, git, worktree, session-adopt, data-loss]
status: fix-deployed
severity: P0
task: AI-CLI-200
---

# [AI-CLI-200] Worktree path-namespace collision

**Status:** fix-deployed

**Severity:** P0 (data loss)

**Created:** 2026-08-08

**Task:** `AI-CLI-200`

## Symptoms

Two symptoms, one cause.

1. `ai session-adopt <name>` reported a successful adoption into
   `.worktrees/<name>` when that directory held none of the repository's content.
   The transcript's recorded working directories were rewritten to point there.
   The only signal in `-n/--dry-run` was the **absence** of a "worktree to create"
   line.
2. `create_worktree` was one condition away from `shutil.rmtree`-ing a directory
   holding nested git worktrees, including commits that existed nowhere else.

Observed live: `.worktrees/<session>` was a plain directory holding four nested
agent worktrees and no `.git` of its own.

## Root cause: two subsystems, one path

`.worktrees/<name>` carries two incompatible meanings:

* `ai c <name>` treats it as **that session's own git checkout**.
* Tooling that gives an agent its own worktree per task treats it as a
  **container**, spelled `<name>/<task>/<leaf>`.

A session launched from a repository **root** therefore has its agent container
sitting exactly where its own checkout must go. Sessions that ran from a repo root
are precisely the population `ai session-adopt` exists to migrate, so its primary
use case structurally guarantees meeting this.

### Defect A — `session_adopt._ensure_worktree`

```python
wt_dir = repo_root / ".worktrees" / ai_name
if wt_dir.is_dir():
    return wt_dir, False
```

`is_dir()` cannot distinguish a git checkout from any other directory of that
name.

### Defect B — `session.create_worktree`

```python
if str(wt_dir) in res.stdout:  # git worktree list --porcelain
    ...
    return wt_dir
shutil.rmtree(wt_dir, ignore_errors=True)
```

Two problems in four lines:

1. **Substring test.** `…/.worktrees/name` is a substring of the porcelain line
   `worktree …/.worktrees/name/leaf`, so a container with no checkout of its own
   reported as registered.
2. **Unguarded deletion.** The `else` branch is an unconditional `rmtree`. Reached
   with a container in place, it takes every nested worktree with it.

**The two defects masked each other.** The deletion was never reached only because
the substring happened to match, so the safe outcome was luck rather than
correctness — and fixing only the substring test would have armed the deletion.

## Reproduction

Against a real repository (`tests/test_worktree_container_collision.py` automates
this):

```bash
git worktree add .worktrees/session-1/agent-a --detach
git worktree list --porcelain | grep -F ".worktrees/session-1"     # matches
git worktree list --porcelain | grep -Fx "worktree $PWD/.worktrees/session-1"  # no match
ls -a .worktrees/session-1                                          # no .git
```

The two greps are the discriminating probe: a substring search answers
"registered" for the container, and an exact-line search does not.

## Fix

* `session.registered_worktrees(repo_root)` parses `git worktree list
  --porcelain` **line-exactly** on the `worktree ` prefix and resolves each path,
  so a parent directory of a worktree can never read as one.
* `create_worktree` refuses to delete any directory containing a git checkout —
  registered or not, since an unregistered clone is invisible to `git worktree
  list` yet still holds commits. The `RuntimeError` names the colliding paths and
  the `git worktree move` that relocates them.
* `_ensure_worktree` verifies its destination is a registered worktree **of that
  repository** and otherwise raises `AdoptionError`, in the dry run as well as the
  real run.
* Nothing is relocated automatically. Moving a human's unpushed work is a human
  decision.
* A stale directory holding no checkout is still deleted and recreated, so a
  leftover from an interrupted run cannot block a session forever.

## Verification

Each guard was watched failing before it was trusted:

* Reverting `_ensure_worktree` to `is_dir()` turns its four regression tests red.
* Restoring the substring test, or neutering the checkout guard, turns three
  `create_worktree` tests red.
* Positive controls in both suites confirm the safe paths still work: a genuine
  worktree is reused untouched, and a debris-only directory is still replaced.

## Lessons Learned

* **A substring test on command output is a defect waiting for a longer path.**
  Any tool that prints one path per line wants exact-line matching. State what the
  probe would print if the conclusion were false: here, an identical answer.
* **Two bugs can hide each other, and the survivor is the dangerous one.** The
  deletion looked safe only because a second defect kept control away from it.
  Fixing the visible one first would have caused the data loss.
* **A dry run whose only failure signal is a missing line is not a preview.**
  Refusals must fire in the preview path, or the preview certifies the very state
  it should have caught.
