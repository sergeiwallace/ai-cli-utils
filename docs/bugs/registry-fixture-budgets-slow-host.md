---
title: "Name-registry tests error at setup on a host where one dolt push takes over 60 s"
category: bug
tags: [bug, tests, naming, dolt, slow-host]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Name-registry tests error at setup on a host where one dolt push takes over 60 s

**Status:** resolved

**Severity:** P2

**Created:** 2026-11-18

**Task:** AI-CLI-ttrf

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

A verbose full run on a managed macOS host (2026-11-17) reported 81 FAILED or ERROR
rows across 26 files, nearly all name-registry tests. CI passed the same commit on
Linux, macOS and Windows. 56 of about 65 error lines were one of:

```text
ai_cli.name_registry.RegistryUnreachableError: `dolt push` did not finish within 60 s; its last output: - Uploading...
ai_cli.name_registry.RegistryUnreachableError: `dolt sql` did not finish within 15 s
```

The counts by budget were 47 push and 4 fetch at 60 s, 13 local calls (`sql`, `reset`,
`remote`, `push`) at 15 s, and one `sql` at 4 s.

## Environment

An enterprise-managed macOS host on Apple Silicon, Dolt 2.3.5. Endpoint security makes
every process spawn cost 0.5 to 6 s; `/usr/bin/true` took 0.9 to 1.1 s on 2026-11-18.

## Reproduction Steps

1. In a checkout at `48f274d1`, run
   `pytest tests/test_name_dolt.py -n 0 -p no:randomly`.
2. Observe 1 failed and 31 errors in 1 h 15 min. Every error is at setup, from
   `build_fleet` calling `DoltRegistry.initialize`, whose first push hits the 60 s
   budget.
3. Run one of those tests alone
   (`test_given_a_compiled_change_when_inspected_then_every_mutating_statement_carries_its_whole_guard`).
   It errors the same way after 96 s of setup, so the failure does not depend on test
   order.

## Root Cause Analysis

Each registry subprocess runs under a budget. The local calls (`init`, `sql`, `reset`,
`remote`, `version`) use the registry's `timeout_s`. The shared fixtures in
`tests/name_fleet.py` set it to 15 s, the same value as the fleet policy's
`publish_timeout_s`. Fetch, push and clone read `[session_names]` keys from
`config.toml` at call time. The suite isolates `XDG_CONFIG_HOME` for every test, so
those calls always get the shipped 60 s defaults, and a slow host's own `config.toml`
cannot widen them.

On this host a real `dolt push` to a bare git remote takes longer than that. Measured
outside pytest with a seeded bare remote and a fresh `DOLT_ROOT_PATH`, three
consecutive pushes took 44 s, 60 s and 87 s. A shim that logged every `git` call made
by one fixture's first push recorded 20 sequential `git` processes before the 60 s
budget killed the push during a `git update-ref`. Each of those processes took 0.3 to
6 s.

Rejected hypotheses:

| Hypothesis | Check | Result |
|---|---|---|
| Only the 2026-11-17 SIGSTOP incident caused it | Re-ran `test_name_dolt.py` serially on 2026-11-18 after recovery | Rejected: 31 errors, same message |
| An earlier test leaves state that slows later ones | Ran one erroring test alone | Rejected: it errors alone |
| pytest's environment makes dolt slower than a shell | Timed the same push in a plain shell | Rejected: 44 to 87 s there too |
| My first shell probe (8 s pushes) showed dolt is fast | Read the probe's push output | Rejected: those pushes failed fast ("git remote has no branches"); the probe could not fail visibly |

The 60 s product default is still right for the fleet. A slow host raises
`[session_names] registry_push_timeout_seconds` in its own config, which the product
already reads on every push. The defect was in the tests: they asserted logic while
running the real program under budgets sized for a fast host, with no way for the host
to say otherwise.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11-13 | Quiet full run to name the failures | Six failures stayed unnamed under random order and quiet output |
| 2 | 2026-11-17 | Verbose full run with two workers | Named 81 rows; the run took 9 h because of a host-wide SIGSTOP incident |

## Fix

`tests/name_fleet.py` now has `REGISTRY_TEST_TIMEOUT_S = 170`. The fixture registries
get it as `timeout_s`, the fixture policy gets it as `publish_timeout_s`, and
`build_fleet` raises the three fetch, push and clone defaults in `ai_cli.config` to it
for the test. 170 s is about twice the slowest measured push. The host lock waits that
budget times the policy's 5 attempts, which is 850 s. That stays under conftest's 900 s
per-test Dolt bound, so a hung call or a held lock still fails inside its test and names
the cause. A first draft used 300 s; review caught that its 1500 s lock wait outran the
per-test bound. The product defaults are unchanged. The one test that asserts them
(`test_given_no_clone_timeout_setting_...`) first calls `shipped_budget_defaults`.

A child process that a test starts with `sys.executable` reads the policy file, so it
gets the widened `timeout_s`. It does not see the monkeypatched fetch, push and clone
defaults and keeps the shipped 60 s. Three tests start such children:
`test_given_two_processes_on_one_host_...` in `test_name_dolt.py`, the release-race
tests in `test_name_release.py`, and a pty test in `test_name_prompt.py`. On the slow
host the Dolt case of each errored at fixture setup before its child ran, so whether a
child also needs the wider budget there is not yet measured.

`test_given_distinct_fetch_and_push_timeouts_when_writing_then_each_dolt_call_gets_only_its_own`
set the publish budget to 4 s and slept 5 s before each fetch and push. The sleep ran
before `subprocess.run`, so it never reached a timeout. The 4 s budget also bounded
every local `dolt sql`, which on this host failed with "`dolt sql` did not finish
within 4 s". The test now keeps only its recorded-budget assertions, with fetch and
push budgets of 401 s and 407 s.

## Verification

The regression test is
`tests/test_name_fleet_budgets.py::test_given_a_host_where_one_dolt_push_takes_87_s_when_the_fixtures_build_a_registry_then_every_budget_outlasts_it`.
It checks every budget a fixture registry gets, including the host lock's wait,
whether the fixture built the registry or discovery read it from the policy. Each must
exceed the measured 87 s and stay under the 900 s per-test bound. On the unfixed
fixtures (base `7fb47ecc`) it failed with `('git fixture', 'timeout_s', 15)`,
`assert 15 > 87`, and it passes after the fix.

On the slow host, rebased on `7fb47ecc`, with the 170 s budget:

```text
pytest tests/test_name_fleet_budgets.py \
  tests/test_name_dolt.py::test_given_a_compiled_change_when_inspected_then_every_mutating_statement_carries_its_whole_guard \
  tests/test_name_dolt.py::test_given_distinct_fetch_and_push_timeouts_when_writing_then_each_dolt_call_gets_only_its_own \
  tests/test_name_dolt.py::test_given_no_clone_timeout_setting_when_cloning_then_windows_waits_longer_than_other_platforms \
  -n 0 -p no:randomly
4 passed in 1758.57s
```

The compiled-change test errored at setup before the fix. After the fix its setup alone
took 498 s, and the distinct-timeouts test took 475 s of setup plus 224 s of call.

Whole-file runs of `test_name_dolt.py`, `test_name_import.py` and `test_session_title.py`
on the same host were killed by pytest-timeout, at the 300 s bound for a git-only
fixture and at the 900 s Dolt bound. During those runs the host read load 22.97 on 10
cores and a 4.0 s spawn canary. Two runaway processes outside this repository were
each holding most of a core at the time. Those runs measure host load, not these
budgets: a git-only fixture timed out inside a single `git rev-parse`. The suite's
per-test bounds are sized for CI, and this change leaves them alone.

## Lessons Learned

A test that runs a real program measures the host as well as the code. Each fixed
subprocess budget in a fixture assumes how fast the host is. Size it from the slowest
host measured, and keep it below the per-test bound so a hang is still reported.

A probe has to be able to fail visibly. The first shell timing reported 8 s pushes
because each push had failed fast, and only reading the push output showed that.

<!-- /doc:region name="summary" -->

<!-- doc:region name="fix_log" kind="append_only" -->

| Date | Commit | Change |
|---|---|---|
| 2026-11-18 | `d977f04a` | RED regression test for the fixture budgets and the host lock's wait |
| 2026-11-18 | `7cd7b276` | Fixture budgets raised to 170 s; the distinct-timeouts test no longer depends on a 4 s local budget |

<!-- /doc:region name="fix_log" -->
