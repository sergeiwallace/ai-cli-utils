---
title: "Per-Test pytest Memory Guard Opt-Out"
category: bug
tags: [bug, pytest, memory]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Per-Test pytest Memory Guard Opt-Out

## Founding Ask Coverage

**Status:** resolved

**Severity:** P2

**Created:** 2026-09-19

**Task:** TPL-r68

The founding ask is covered by a Linux-only `unconstrained_memory` pytest marker. It removes the
guard's `RLIMIT_AS` ceiling only while the marked test runs and restores the ceiling before the
next test. The canonical template is changed here; downstream propagation and adoption by the
real Chrome boundary test are a separate tracked follow-up.

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

A real Chrome launch in a downstream pytest suite could terminate with `SIGTRAP` before writing
stdout or stderr when the default Linux `RLIMIT_AS` ceiling was active. The affected test honestly
skipped after detecting the finite limit, but could therefore never exercise its real subprocess
boundary.

## Environment

Linux pytest processes load `pytest_memory_guard.py`. The guard applies a default 2048 MiB address
space limit, or the lower `PYTEST_WORKER_MEMORY_LIMIT_MB` value supplied by the test environment.

## Reproduction Steps

1. Run pytest with `pytest_memory_guard` and a tight `PYTEST_WORKER_MEMORY_LIMIT_MB` value.
2. Run a test that needs a large virtual-address-space allocation or launches a memory-hungry real
   subprocess.
3. Observe the process fail under `RLIMIT_AS`; before this fix no individual test could lift the
   ceiling.

## Root Cause Analysis

`pytest_configure()` applied `RLIMIT_AS` to the entire Linux pytest process before collection, so
it could not inspect a test marker. It also set both the soft and hard limits to the guard value.
Linux does not permit an unprivileged process to raise that hard limit later, making a per-test
opt-out impossible after configuration.

The confirmed causal chain was: default process-wide guard limit, inherited by real subprocesses,
then a memory-hungry startup reservation exceeding that limit, then Chrome's early `SIGTRAP`.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-09-19 | Keep the existing hard limit and raise it for a marked test. | Rejected: an unprivileged Linux process cannot raise the existing reduced hard limit. |

## Fix

The Linux guard now preserves the inherited hard `RLIMIT_AS` value and applies its configured
ceiling as the soft limit. It registers `@pytest.mark.unconstrained_memory` and wraps the complete
marked test protocol: the hook raises the soft limit to the inherited hard limit for that test, then
restores the configured guard ceiling in `finally` before any later test runs.

The portable macOS and Windows RSS watchdog path remains unchanged. Tests without the marker retain
the existing default guard behavior.

## Verification

- [x] A real pytest xdist subprocess without the marker is stopped before its 256 MiB allocation
  completes under a 384 MiB ceiling.
- [x] The same allocation completes in a real pytest xdist subprocess with
  `@pytest.mark.unconstrained_memory`.
- [x] A following unmarked test in that same xdist worker is stopped, proving the soft limit is
  restored after the marked test.
- [ ] Repository hard gate: `uv run --extra test` cannot fetch the pinned `fleet-testkit` Git
  dependency while GitHub DNS is unavailable. The available environment's full pytest run instead
  reported 154 passing tests and one pre-existing consumer-fleet assertion failure for missing
  `app-portal` files. The prescribed ruff command also names an absent `src/` directory and reports
  unrelated existing lint findings outside this change.

## Lessons Learned

Process-wide resource limits need a restoration path when a single test legitimately crosses the
default safety boundary. A real subprocess regression must prove both sides: the explicit opt-out
works and the next ordinary test remains constrained.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Role / effort | Change | Result |
|------|---------------|--------|--------|
| 2026-09-19 | `implement` / `high` | Preserved the Linux hard limit, added the per-test marker hook and real xdist subprocess regressions. | Focused canonical tests pass. |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
