---
title: "Copier Update Local Source Origin Lag"
category: bug
tags: [bug, copier, git, template]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Copier Update Local Source Origin Lag

## Founding Ask Coverage

**Status:** resolved

**Severity:** P1

**Created:** 2026-10-17

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

`ai copier-update` reported a successful update for projects whose `_src_path` named a local
template checkout, but rendered the commit at that checkout's `HEAD`. If the checkout was behind
its origin, those projects ended one template commit behind projects using a URL source.

## Environment

The defect affected relative paths, absolute paths, and bare names that resolve to a local Git
checkout. URL sources already cloned their remote and were not affected by the stale local `HEAD`.

## Reproduction Steps

1. Create a bare template origin and push an initial template commit.
2. Clone it as a local template checkout.
3. Create a Copier-managed project whose `_src_path` names that checkout.
4. Push a second template commit to the bare origin without advancing the local checkout's `HEAD`.
5. Run `ai copier-update --no-isolate` for the project.

On the unfixed code, the command exited successfully while the rendered file and stored template
commit remained at the initial commit. The regression test
`test_given_origin_ahead_of_local_source_when_updating_then_project_uses_origin_commit` failed with
`original` instead of `updated from origin`.

## Root Cause Analysis

The source resolver converted a relative or bare local source to an absolute path and returned that
path directly. Template parity inspection then compared the project's previous commit to that
checkout's `HEAD`, and Copier received the same path with `--vcs-ref HEAD`. Neither path fetched the
checkout's origin or resolved its default branch.

The causal chain was:

```text
local or bare-name _src_path
  -> source resolver returns the shared checkout path
  -> parity inspection and Copier both read the checkout's stale HEAD
  -> the run reports success without applying the origin's newer template commit
```

The hypothesis that the isolated project worktree selected the stale commit was rejected: source
resolution occurs before that worktree is created, and the resolved local checkout path is passed
through unchanged. The hypothesis that uncommitted files in the template checkout caused the lag
was also rejected: the deterministic reproduction used a clean checkout one commit behind origin.

The fix remained bug-sized: it changes one source-resolution component, introduces no public API,
and does not cross a repository or architectural boundary. The implementation role was `implement`
at medium effort because the Git/filesystem boundary required real integration coverage but no
redesign.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-10-17 | Resolve relative and bare names to an absolute checkout path. | Fixed working-directory resolution but still used the checkout's stale `HEAD`. |
| 2 | 2026-10-17 | Attribute the lag to isolated project worktrees. | Rejected because direct mode reproduced the same stale render. |

## Fix

Before updating projects, the command now prepares every distinct local template checkout once:

1. Fetch the checkout's `origin` without changing its branch, index, or working tree.
2. Resolve the origin's advertised default branch and its fetched remote-tracking commit.
3. Create a run-owned temporary clone and detach it at that exact commit.
4. Reuse that snapshot for every project naming the same checkout.
5. Clean the run-owned temporary source directory when the run ends.

If fetch, default-branch resolution, or snapshot creation fails, affected projects are refused with
a named reason. There is no fallback to the local checkout's `HEAD`. Each local-source project result
also reports the selected template commit.

## Verification

- [x] RED: the origin-ahead regression failed on the unfixed code with the project still rendering
  `original`.
- [x] The same real Git/Copier test passes after the fix and records the origin commit.
- [x] A bare-name source renders the origin commit and preserves the stored bare spelling.
- [x] A fetch failure returns a temporary-failure status and leaves the project byte-for-byte
  untouched.
- [x] A dirty shared template checkout retains the same `HEAD`, status, tracked bytes, and untracked
  file after a successful run.
- [x] `uv run pytest -q tests/test_copier_update.py tests/test_copier_update_recovery.py
  tests/test_copier_update_memory.py` — 93 passed.
- [x] `uv run ruff check .` and `uv run ruff format --check .` — clean after the implementation.
- [ ] A full repository run attempted before the final fixture updates exceeded five minutes at 98%
  and was interrupted at the task's requested time bound. It included local-source contract fixture
  failures that were corrected afterward; the final complete full run was not repeated.

## Lessons Learned

Resolving a source path proves only where a checkout lives, not which remote commit should be
rendered. Automation that promises fleet-wide convergence must pin local sources to an explicit
remote commit and report that commit, while treating a fetch failure as a refusal rather than an
offline fallback.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-10-17 | Added real origin-ahead, bare-name, refusal, and checkout-immutability regressions. | RED on stale local `HEAD`; GREEN after the fix. |
| 2026-10-17 | Prepared and reused run-scoped local-source snapshots at origin's default-branch commit. | 93 focused tests pass; Ruff clean. |

<!-- /doc:region name="summary" -->
