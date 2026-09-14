---
title: "Copier template config mock causes runaway memory allocation"
category: bug
tags: [bug, copier, pytest, yaml, memory]
status: resolved
template_version: "bug-1.0.0"
---

# Copier template config mock causes runaway memory allocation

## Founding Ask Coverage

`founding_ask_ref`: N/A — this Tier 0 repair was dispatched from an incident brief without a
canonical founding-ask record.

**Status:** resolved

**Severity:** P0 — repeated test runs exhausted host memory and made a shared machine unresponsive

**Created:** 2026-09-13

<!-- doc:region name="summary" kind="replaceable" -->

## Table of Contents

- [Summary](#summary)
- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Scope Decision](#scope-decision)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Frozen Regression Test](#frozen-regression-test)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

## Summary

Reading a Copier template's `_subdirectory` exposed four existing tests to unbounded mock
allocation. Those tests returned a `MagicMock` from every mocked `subprocess.run` call without
giving it text for `stdout`. PyYAML treated that mock as a readable stream and repeatedly created
more mocks until the process exhausted its memory allowance. The config reader now rejects
non-text subprocess output and non-mapping YAML before using either value.

## Symptoms

Four individual tests grew from a normal baseline of about 55 MiB RSS to approximately 1.81 GiB
RSS, then failed with `MemoryError` under the 2 GiB address-space guard. Under pytest-xdist, a
reused worker could grow far beyond the host's safe capacity before the guard was introduced.

A fifth test failed quickly with `AttributeError: 'str' object has no attribute 'get'` because its
broad subprocess mock returned a YAML scalar for the config lookup.

## Environment

- Linux, Python 3.14
- pytest with 16 automatically selected xdist workers
- A 2 GiB `RLIMIT_AS` ceiling active in every test process
- PyYAML parsing subprocess output and `unittest.mock.MagicMock` providing incomplete results

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

Each test was run in its own process with xdist disabled and the memory guard explicitly loaded:

```text
PYTEST_WORKER_MEMORY_LIMIT_MB=2048 /usr/bin/time -v pytest \
  -o 'addopts=-p pytest_memory_guard' -p no:xdist -q \
  tests/test_copier_update.py::test_run_copier_update_success
```

The same procedure was applied to all 54 tests in the file. Four tests reproduced the runaway
independently:

| Test | Peak RSS | Result before fix |
| --- | ---: | --- |
| `test_run_copier_update_success` | 1,852,072 KiB | `MemoryError` |
| `test_run_copier_update_uses_vcs_ref_head` | 1,851,900 KiB | `MemoryError` |
| `test_run_copier_update_conflict_markers` | 1,850,792 KiB | `MemoryError` |
| `test_run_copier_update_partial_failure` | 1,851,208 KiB | `MemoryError` |

The new subdirectory-parity behavior test passed independently at 54,936 KiB RSS, disproving the
hypothesis that its real Git repositories or temporary directories were the allocator.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

The causal chain is:

```text
template subdirectory lookup adds a Git subprocess call
  -> an existing broad subprocess mock returns MagicMock stdout
  -> yaml.safe_load treats that value as a stream
  -> PyYAML repeatedly invokes MagicMock.read(4096)
  -> every call constructs and retains more mock objects
  -> the test process grows until memory allocation fails
```

A 0.25-second tracemalloc sample of `yaml.safe_load(MagicMock())` observed 109 calls to
`read(4096)`. It attributed 25,802,490 retained bytes across 132,302 blocks to
`unittest/mock.py`. This runtime evidence identifies the allocator rather than inferring it from
the failing source line.

| Hypothesis | Discriminating evidence | Result |
| --- | --- | --- |
| The new real-Git parity test leaks repositories or subprocess handles | It completed at 54,936 KiB RSS in isolation | Rejected |
| Memory accumulates only because xdist reuses a worker | Four old tests reached about 1.81 GiB independently | Rejected |
| PyYAML consumes incomplete mock output as an endless stream | Tracemalloc observed repeated `MagicMock.read(4096)` calls and mock allocations | Confirmed |
| A YAML mapping remains safe regardless of subprocess output type | A scalar config caused `.get` to raise | Rejected |

## Scope Decision

All bug-fix scope signals are no: the fix changes one internal parser boundary, introduces no
shared abstraction, changes no public API, crosses no repository boundary, and does not broaden a
known cross-cutting pattern. The validation is reversible and low-blast-radius, so a contained fix
is appropriate.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
| --- | --- | --- | --- |
| 1 | 2026-09-13 | Treated the incident as potentially cumulative across reused xdist workers | Rejected by isolated single-test reproductions |
| 2 | 2026-09-13 | Profiled the full failing test with tracemalloc under a reduced address-space limit | Tracemalloc overhead exhausted the limit before useful attribution |
| 3 | 2026-09-13 | Profiled the suspected parser input directly with a timed tracemalloc sample | Confirmed repeated mock stream reads and retained mock allocations |

## Frozen Regression Test

`test_given_non_text_git_output_when_reading_template_config_then_does_not_parse_stream` supplies
a subprocess result whose non-text `stdout.read()` raises immediately. On unfixed code, the test
failed at PyYAML's stream read with:

```text
AssertionError: non-text stdout was parsed as a stream
```

The test remained unchanged while the production fix was applied.

## Fix

`_template_subdirectory` now returns an empty subdirectory before parsing when Git's captured
output is not text. After parsing, it also returns an empty subdirectory unless the YAML document
is a mapping. Valid Copier mappings retain the existing behavior.

## Verification

- [x] Frozen regression RED: failed at the unwanted stream read, 63,748 KiB peak RSS.
- [x] Frozen regression GREEN: `1 passed`, 55,600 KiB peak RSS.
- [x] All four former runaways pass individually between 53,928 and 55,512 KiB peak RSS.
- [x] The related scalar-config test passes individually at 53,928 KiB peak RSS.
- [x] The full original file completed once with `-n auto`: `54 passed` in 1.22 seconds.
- [x] All 16 xdist workers printed the 2 GiB guard activation message.
- [x] External 10 ms RSS sampling measured a maximum worker peak of 57.8 MiB.

## Lessons Learned

Subprocess mocks must model the result contract for every command that production code can issue;
a broad `MagicMock` is not a harmless empty result when a parser accepts file-like objects. Parser
boundaries should still validate captured output types and document shapes before consuming them,
because malformed adapters and test doubles can otherwise turn an ordinary contract violation
into an unbounded allocation. Memory-sensitive regressions should include an immediate sentinel
failure so RED evidence can be collected without recreating a host-threatening allocation.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
| --- | --- | --- |
| 2026-09-13 | Pending | Added non-text and non-mapping config guards plus focused regression coverage |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

The final xdist monitor reported 16 workers with individual peaks from 56.1 to 57.8 MiB. The full
file exited successfully without a worker crash, `INTERNALERROR`, or memory-guard termination.

<!-- /doc:region name="appendix_evidence" -->
