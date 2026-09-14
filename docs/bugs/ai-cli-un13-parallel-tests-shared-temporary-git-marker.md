---
title: Parallel tests misidentify shared temporary Git marker
category: bug
tags: [bug, tests, pytest, xdist, git]
status: fixed
source: "tracked bug report"
template_version: "bug-1.0.0"
task: AI-CLI-un13
---

# Parallel tests misidentify shared temporary Git marker

## Founding Ask Coverage

`founding_ask_ref`: N/A — this was a fidelity-tier-0 bug-fix dispatch with no founding ask.

**Status:** resolved

**Severity:** P2 — the default parallel test command reported 16 false failures

**Created:** 2026-09-13

<!-- doc:region name="summary" kind="replaceable" -->

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Table of Contents](#table-of-contents)
- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Scope Decision](#scope-decision)
- [Frozen Regression Test](#frozen-regression-test)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

## Summary

The default parallel pytest run failed 16 tests in prefix resolution, session audit, and local project launch. Pytest's
per-worker paths were isolated correctly, but every path was nested under a shared temporary root that exposed an empty
`.git` placeholder. Two repository ancestor walkers accepted any filesystem entry named `.git`, so the placeholder made
unrelated worker paths appear to belong to the temporary root. The fix requires a marker to have the shape of a real Git
checkout before treating its parent as a repository.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

On Linux with Python 3.14.7, pytest 9.1.1, and pytest-xdist 3.8.0, run the affected surface through the repository's
parallel default:

```shell
pytest -n auto tests/test_config.py::TestProjectPrefixRegistry \
  tests/test_prefix_registry_tiers.py tests/test_session_audit.py \
  tests/test_cli.py::TestLocalProjectChdir
```

The unfixed revision reported `16 failed, 87 passed`. Failures consistently resolved worker-specific paths such as
`/tmp/pytest-of-user/pytest-N/popen-gw6/.../myproject` to `/tmp`. Inspecting `/tmp/.git` showed an empty, read-only
directory rather than a Git repository.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

The causal chain was:

```text
parallel workers create isolated paths beneath a shared temporary ancestor
  -> that ancestor exposes an empty entry named .git
  -> repository discovery treats .git existence alone as authoritative
  -> every worker path collapses to the shared ancestor
  -> registry lookups, registration writes, and audit classifications use the wrong root
  -> 16 otherwise independent tests fail
```

| Hypothesis | Evidence | Result |
| --- | --- | --- |
| Workers share an XDG registry or prefix cache | The failing paths were worker-isolated, and the wrong root was selected before registry lookup | Rejected |
| The module-level registry cache leaks across tests | The cache is reset by an autouse fixture and cannot cross worker processes | Rejected |
| A shared ancestor's `.git` placeholder is accepted as a repository | Every wrong root was `/tmp`; its `.git` was empty; a minimal empty-marker test reproduced both boundary failures | Confirmed |

## Scope Decision

The public-contract, cross-repository, and three-unrelated-subsystem signals are **no**. The fix does introduce one small
shared internal predicate so both ancestor walkers use identical repository-marker semantics; that single signal does not
meet the redesign threshold. The change is contained, reversible, and has a narrow blast radius, so a bug fix is the
appropriate scope.

## Frozen Regression Test

Two behavior-level tests create an empty `.git` directory above an otherwise unrelated path. One exercises public prefix
resolution and asserts that the intended repository is registered while the false ancestor is not. The other exercises
session-audit ownership and asserts that the path remains unowned. Before the production edit they failed as follows:

```text
ProjectPrefixError: No task prefix is registered for repository .../shared
AssertionError: (.../shared, 'repo-subdir') != (None, 'unknown')
```

The tests were frozen before the implementation changed and were not modified during the fix.

## Fix

`config._has_valid_git_marker` now recognizes the two real working-tree marker shapes:

- a normal checkout's `.git` directory must contain a `HEAD` file;
- a linked worktree's `.git` file must begin with `gitdir:`.

Both prefix root resolution and session-audit ownership use this predicate. Empty placeholders and unrelated files named
`.git` are ignored, while normal checkouts and linked worktrees retain their behavior. A repository-wide grep found no
other ancestor walker with the faulty existence-only test; other `.git` checks inspect an already-selected directory for
worktree or deletion guards and do not perform ancestor ownership discovery.

## Verification

- Frozen regression, unfixed: `2 failed` for the expected incorrect-ancestor behavior.
- Frozen regression, fixed: `2 passed` under two xdist workers.
- Original affected surface plus preservation coverage: `106 passed` under 16 xdist workers, with no related failures.
- Repository gate: the final `pytest -n auto` run executed 99% of 2,903 tests and showed no target-family failures, but
  displayed unrelated failures and then repeated the baseline teardown hang; it was interrupted after several minutes.

## Lessons Learned

Temporary-path isolation prevents workers from writing the same leaf path, but it cannot protect code that walks upward
and trusts a shared ancestor. Filesystem entry names are not sufficient proof of repository ownership. Tests for
ancestor discovery should include both a valid nearer marker and an invalid shared marker so environment-provided mounts
or placeholders cannot silently redefine the test's project root.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
| --- | --- | --- |
| 2026-09-13 | this change | Codex / implement / fidelity tier 0 / xhigh / GPT-5; concurrent failure required runtime discrimination across workers |
| 2026-09-13 | this change | Added and froze two empty-marker regressions; RED for the expected wrong-root behavior |
| 2026-09-13 | this change | Validated Git marker shape in both ancestor walkers; focused regressions and affected parallel surface GREEN |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

The first bare `pytest` command resolved to an unrelated environment and failed before collection because `psutil` was
missing. An offline environment bootstrap also failed because an uncached `ruff` wheel was unavailable. Neither result
was treated as reproduction evidence. The valid reproduction and verification commands used this repository's existing
environment explicitly.

The first full-suite reproduction reached the final progress percentile but hung during teardown after showing many
failures, so it was interrupted. The focused affected-surface run then provided the deterministic 16-failure baseline
and complete tracebacks used for diagnosis.

<!-- /doc:region name="appendix_evidence" -->
