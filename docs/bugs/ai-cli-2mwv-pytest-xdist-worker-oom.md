---
title: "Unlimited pytest-xdist workers could exhaust a shared host"
category: bug
tags: [bug, tests, pytest, xdist, memory, resource-limit]
status: fixed
source: "AI-CLI-2mwv"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Unlimited pytest-xdist workers could exhaust a shared host

**Status:** fixed — every test process now has a memory ceiling; the incident's triggering test remains unidentified

**Severity:** P0 — one test run exhausted host RAM and swap and made a shared machine unresponsive

**Created:** 2026-09-13

## Summary

The repository configured every bare pytest invocation with `-n auto`, but placed no memory ceiling on the resulting
workers. During the incident, two execnet worker processes grew to approximately 44–46 GiB RSS each within five minutes.
Together they exhausted 8 GiB of swap and most of a 125 GiB host's RAM.

The exact allocating test could not be reproduced on an eight-core macOS host. A bounded, instrumented `-n auto` run
completed in 91.49 seconds; every pytest process stayed below 185 MiB RSS. The run had 41 unrelated baseline failures
caused by sandbox permissions, platform assumptions, inherited session state, and pre-existing public-hygiene findings.
No memory guard fired.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

The live incident established the dangerous state directly: both 44–46 GiB processes used execnet's
`python -u -c "import sys;exec(eval(sys.stdin.readline()))"` bootstrap, identifying them as pytest-xdist workers. The
repository configuration supplied `-n auto`, so pytest created one unlimited worker per detected CPU.

The local diagnostic run loaded a temporary monitoring plugin, retained the incident's `-n auto` mode, sampled each
pytest process's own RSS, and stopped any process at 1 GiB. It collected all 2,900 tests without a memory stop:

```text
41 failed, 2,843 passed, 16 skipped; 91.49s
maximum observed pytest-process RSS: 185 MiB
```

A source scan found no existing test body with an unbounded Python allocation. The generated-supervisor tests do retain
subprocess output through `communicate()`, which is structurally capable of accumulating child output, but that worker
also stayed bounded locally. Without a matching RSS rise, stack trace, or retained process, this remains a hypothesis and
was not patched.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause Analysis

The proven causal chain is:

```text
an unidentified test path allocates continuously
  -> its pytest worker has no address-space or RSS limit
  -> the worker grows to tens of GiB
  -> `-n auto` permits multiple independent workers to do the same
  -> aggregate RAM and swap exhaustion freezes the shared host
```

`-n auto` is an amplifier, not the allocator. Removing parallelism would reduce CI throughput and would still leave a
serial runaway able to consume the host. The causal configuration defect is that test processes were unlimited.

| Hypothesis | Evidence | Result |
| --- | --- | --- |
| Application code spawned the observed `stdin.readline()` process | The exact command is execnet's worker bootstrap | Rejected |
| One deterministic test leaks on every host | All local workers completed below 185 MiB | Not reproduced |
| A generated supervisor's captured output is the allocator | The helper uses pipes and `communicate()`, but its local worker remained bounded | Unconfirmed |
| Unlimited xdist workers allowed host-wide exhaustion | Live workers reached 44–46 GiB and config had no ceiling | Confirmed |

### Scope decision

All Step 2.5 scope signals are **no**: the fix stays inside pytest configuration, adds no public API, crosses no repository
boundary, and introduces no shared production abstraction. It is reversible and low-blast-radius, so a contained bug fix
is appropriate.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="options" kind="replaceable" -->

## The Frozen Regression Test

`test_given_runaway_xdist_worker_when_memory_limit_is_reached_then_worker_is_stopped` launches a real one-worker xdist
run. Its synthetic test allocates a bounded 256 MiB, prints a completion sentinel, and pauses. With the unfixed config,
the nested run exited 0 and printed the sentinel; the outer regression failed in 1.59 seconds for the intended reason:

```text
AssertionError: the runaway xdist worker completed despite exceeding its memory ceiling
```

The test was frozen before the implementation changed and was not modified during the fix.

## Fix

`pytest_memory_guard.py` is loaded from pytest's existing `addopts` while preserving `-n auto`.

- Linux test processes receive a kernel-enforced 2 GiB `RLIMIT_AS` ceiling.
- macOS and Windows use an independent `psutil` watchdog with a 2 GiB RSS ceiling. macOS cannot use a practical
  `RLIMIT_AS` here because a fresh Python process maps approximately 445 GiB of virtual address space; Windows has no
  `resource` module.
- The xdist controller is excluded; every xdist worker is guarded. A serial pytest process is guarded too.
- `PYTEST_WORKER_MEMORY_LIMIT_MB` provides a validated override for regression testing and unusually memory-intensive
  suites; values below 64 MiB are rejected.

The non-Linux watchdog runs outside the worker, so it remains schedulable even if a runaway test monopolizes the Python
GIL. It checks RSS every 50 milliseconds and terminates only its exact parent process.

## Verification

The frozen regression is GREEN: `uv run pytest -n 0 tests/test_pytest_memory_guard.py -q` reported `1 passed in 0.50s`.
A direct one-worker xdist exercise at 192 MiB produced:

```text
pytest memory guard: process exceeded its 192 MiB RSS ceiling (308 MiB); terminating it
worker 'gw0' crashed while running '::test_synthetic_runaway_allocation'
1 failed in 0.26s; exit 1
```

The synthetic completion sentinel was absent. The 116 MiB overshoot came from the intentionally fast bounded allocation
between watchdog samples; the observed incident grew far more slowly. Linux uses a synchronous kernel ceiling and does
not have this polling overshoot.

The repository-level `pytest -q --tb=no` run retained `-n auto` and finished in 64.16 seconds with 2,845 passed, 16
skipped, 40 pre-existing failures, and 6 warnings. No memory guard fired. The gate is not green in this restricted macOS
worktree: its failures reproduce the baseline categories above and include denied access outside the worktree,
Linux-specific loader assumptions, an incompatible BSD `touch` argument, inherited session state, and pre-existing
public-hygiene findings. Focused lint and formatting checks passed.

<!-- /doc:region name="options" -->

<!-- doc:region name="lessons" kind="replaceable" -->

## Lessons Learned

1. Automatic worker count and worker containment solve different problems. Parallelism may remain automatic, but every
   worker still needs an independent ceiling.
2. A timeout bounds elapsed time, not allocation rate. A worker can exhaust a host well before its test timeout expires.
3. An execnet bootstrap command identifies the process class, not the test that was running. Per-test attribution must be
   captured before a worker is killed if the underlying allocator is to be fixed later.
4. Cross-platform containment needs different primitives: Linux address-space limits are hard and synchronous; portable
   RSS monitoring has a small sampling overshoot but closes the macOS and Windows protection gap.

## Fix Log

| Date | Attempt | Result |
| --- | --- | --- |
| 2026-09-13 | Instrumented full `-n auto` run with a 1 GiB emergency stop | No runaway reproduced; all processes below 185 MiB |
| 2026-09-13 | Added frozen synthetic xdist allocation regression | RED: nested worker completed and exited 0 |
| 2026-09-13 | Added the cross-platform per-worker memory guard | GREEN: worker terminated and sentinel absent |

<!-- /doc:region name="lessons" -->
