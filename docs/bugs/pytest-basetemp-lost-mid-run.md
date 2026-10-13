---
title: "A local full-suite run lost its live pytest base temp dir partway through"
category: bug
tags: [bug, tests, pytest, xdist, tmp_path, concurrency]
status: fixed
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A local full-suite run lost its live pytest base temp dir partway through

**Status:** fixed: the suite's temp dirs no longer live in the host-wide shared pytest root. The deleting process was not identified.

**Severity:** P1: a local run reports about 1400 errors that are not test failures, which hides real ones

**Created:** 2026-10-13

## Summary

A local `pytest -n 4` run on macOS ended `3 failed, 2461 passed, 20 skipped, 1406 errors`. Every error was the same
setup failure inside pytest's own `tmp_path` fixture:

```text
FileNotFoundError: [Errno 2] No such file or directory: '<temp>/pytest-of-<user>/pytest-241/popen-gw1'
```

raised from `os.scandir(root)` in `_pytest/pathlib.py:make_numbered_dir`. All four xdist workers lost their
`popen-gwN` directory at the same moment, about 64% into the run, so the whole `pytest-241` base dir was deleted while
the run was live. From then on every test that asks for `tmp_path` errored and every test that does not passed, which
is the alternating `.E.E` progress line. CI on Linux, macOS and Windows was green on the same commit.

## What was ruled out

1. **A test, or code under test, deleting outside its own `tmp_path`.** A full `-n 4` run with a Python audit hook on
   `os.remove`, `os.rmdir`, `os.rename`, `shutil.rmtree` and `shutil.move` logged no removal at the `pytest-of-<user>`,
   `pytest-N` or `popen-gwN` level from any test (`3950 passed, 20 skipped in 2969.59s`). Two concurrent `-n 2` full
   runs with random orders (`--randomly-seed=1111` and `2222`), the hook also installed in every Python subprocess,
   logged none either; after an hour, past the point where the failing run broke, they stood at 3225 and 2701 passed
   with no setup error. A replay of the focused `-n 0` run of `tests/test_session_adopt.py` and
   `tests/test_session_audit.py` that ran beside the failing run logged no removal outside its own base dir except
   pytest's own pruning of an old, unlocked `pytest-N`. A watcher polling the shared root for an hour saw no live
   run lose its base dir.
2. **pytest's own pruning in another pytest process.** pytest keeps the newest three `pytest-N` dirs and deletes older
   ones at exit, but never one whose `.lock` names a live run. A live `-n 2` run survived six other runs that each
   pruned the shared root (`40 passed in 22.31s`). Numbers are reused once the highest dir is removed, which is
   harmless: the deleting run only ever removes its own base dir.
3. **The two genuine `tmp_path` failures** (`test_session_launch_locality` and
   `test_session_launch_shell_resolution`) are consistent with fallout: each found a path it had just created missing, and both
   pass alone. The third failure, `test_workspace`'s `git: 'remote-http' is not a git command`, is unrelated: during that
   run the `git` on `PATH` was briefly a symlink, which makes git look for its helpers beside the link. That test also
   passes alone.

What is left is a process outside this suite deleting a `pytest-N` dir in the shared `pytest-of-<user>` namespace.
That namespace holds the base dir of every pytest run on the host, of every project, so one deletion there reaches
all of them.

<!-- /doc:region -->

## Fix

`tests/conftest.py:pytest_configure` points pytest's own `PYTEST_DEBUG_TEMPROOT` at `suite_temproot()`, the suite's own
root: `<temp>/aipt` on POSIX and the existing short `<drive>/aipt` on Windows. It only does so when the operator has not
set `PYTEST_DEBUG_TEMPROOT` already, and an explicit `--basetemp` still wins inside pytest. pytest's numbered dirs,
`.lock` files and retention work unchanged inside the new root, xdist workers and the inner pytest runs some tests
start inherit it, and on a shared `/tmp` the root is used only if it is a real directory owned by the current user.

## Regression tests

`tests/test_temproot_isolation.py` drives real inner pytest runs, each with a private `TMPDIR` so the shared root they
would otherwise use is a sandbox the test may sweep:

- a fresh run's `tmp_path` is under the suite root, not under `pytest-of-<user>`;
- a live run keeps its `tmp_path` while every `pytest-*` dir in the sandbox's shared root is deleted and a second run
  starts and finishes beside it;
- an explicit `--basetemp` still decides where `tmp_path` goes.

The first two fail when the relocation is removed: the sweep deletes the live run's base dir and the inner test
fails.

## Verification

Two concurrent `-n 2` runs of `tests/test_cli.py`, `tests/test_quota.py` and `tests/test_temproot_isolation.py` held
their base dirs as `<temp>/aipt/pytest-of-<user>/pytest-0` and `pytest-1`, each locked, and both ended
`362 passed, 2 skipped` with no error.
