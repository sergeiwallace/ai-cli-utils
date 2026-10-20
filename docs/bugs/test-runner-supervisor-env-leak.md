---
title: "[AI-CLI-znml] A test run inside a managed session leaks its supervisor generation into fixture supervisors"
category: bugs
tags: [bug, tests, session-supervisor, run-state, macos]
status: fix-deployed
severity: P1
task: AI-CLI-znml
---

# [AI-CLI-znml] Supervisor generation leaks from the test runner

**Status:** fix-deployed

**Severity:** P1 (three run-state tests fail for anyone running the suite from a managed session)

**Created:** 2026-10-19

**Task:** `AI-CLI-znml`

## Symptoms

The three cases of
`tests/test_restart_run_state.py::test_given_old_supervisor_when_ac_i_4_child_refreshes_then_real_final_exit_is_durable`
(`fault` = `none`, `registry`, `both`) failed on macOS at line 119 with
"fixture agent did not start before the deadline". After the test refreshed the
session script and stopped the initial agent, the fixture supervisor never
launched a replacement within 60 s. CI stayed green.

The fixture's `supervisor.log` showed the refreshed child failing in a loop:

```text
[log] general: Error: session state persistence refused: stale supervisor generation
[cli] usage: Error: session state persistence refused; retry before returning to the shell
```

## Root cause

The suite was started from inside a session that `ai` itself supervises. That
supervisor exports `AI_CLI_RUN_GENERATION` (and the `AI_CLI_SUPERVISOR_*`
variables) to everything under it, pytest included. The fixture supervisor is
started with the test process's environment, so it inherited the runner's
generation.

The refreshed child script only acquires its own generation when
`AI_CLI_RUN_GENERATION` is empty, because that is how it recognises a
pre-feature supervisor. With the runner's value present it skipped
`ai sessions _begin`, then called `ai sessions mark … running --generation
<runner's generation>`. The fixture registry holds a different generation, so
`session_run_state.mark` refused every attempt, `_ai_mark_running || exit 76`
fired, and the old supervisor restarted the child forever without ever reaching
the agent launch.

The test already asserts the supervisor has no generation, through
`psutil.Process(supervisor.pid).environ()`. On macOS that call returns `{}` for
Apple platform binaries such as `/bin/bash`, so the assertion passed
vacuously and the leak showed up later as a timeout. On Linux the same leak
would fail that assertion directly. CI runners are not inside a managed session,
so CI never saw it on either platform.

## Reproduction

From a shell inside a managed session (any shell where `AI_CLI_RUN_GENERATION`
is set):

```bash
uv run pytest -q -p no:randomly \
  "tests/test_restart_run_state.py::test_given_old_supervisor_when_ac_i_4_child_refreshes_then_real_final_exit_is_durable"
```

At the base commit this gives 3 failed. With the variable removed from the
runner's environment (`env -u AI_CLI_RUN_GENERATION uv run pytest …`) the same
command gives 3 passed, which isolates the cause.

## Fix

`tests/conftest.py` gains an autouse fixture, `_strip_inherited_supervisor_vars`,
that removes `AI_CLI_RUN_GENERATION` and every `AI_CLI_SUPERVISOR_*` variable for
each test, following the existing `_strip_inherited_direnv_vars` pattern. Several
tests already removed `AI_CLI_RUN_GENERATION` one by one; this covers every test
that spawns a supervisor or a session script.

The failing test also checks `os.environ` directly before its psutil check, so
the precondition holds on macOS instead of passing vacuously. The check compares
the single variable, so a failure never prints the whole environment.

## Verification

* With the fix and the variable set in the runner, the three cases pass.
* With the conftest fixture reverted, the new precondition fails at once with
  `AI_CLI_RUN_GENERATION` present, instead of timing out at line 119.

## Lessons learned

* A test that spawns real child processes inherits the runner's environment.
  Any variable the product exports to its own children has to be stripped at
  the suite level, or the suite behaves differently when run from inside the
  product.
* A precondition read through `psutil.Process.environ()` proves nothing on
  macOS for platform binaries. Check the environment the child was given, not
  the one the kernel will show.
