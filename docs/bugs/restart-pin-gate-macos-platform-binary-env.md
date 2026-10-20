---
title: "[AI-CLI-ds41] The restart pin gate refuses every macOS supervisor"
category: bugs
tags: [bug, restart, macos, supervisor, tmux]
status: fix-in-review
severity: P1
task: AI-CLI-ds41
---

# [AI-CLI-ds41] Restart pin gate refuses every macOS supervisor

**Status:** fix-in-review

**Severity:** P1 (`ai -X` session restart never works on macOS)

**Created:** 2026-10-19

**Task:** `AI-CLI-ds41`

## Symptoms

- On macOS, `ai -X <session>` refused with `supervisor-upgrade-required` and the
  detail `supervisor PATH unavailable`, for supervisors that are current.
- The four `test_given_old_supervisor_when_restart_then_pinned_conversation` cases
  in `tests/test_restart_supervisor.py`, and the spawn-isolation case that re-runs
  them, failed on macOS and passed on Linux.

## Causal mechanism

1. `_restart_pin_gate` decides whether the `ai` the replacement child will resolve
   supports pinned restarts. It reads the supervisor's environment with
   `psutil.Process(pid).environ()`, resolves `ai` on that `PATH` and runs
   `ai internal restart-pin --probe`.
2. macOS withholds the environment of Apple platform binaries from other
   processes. psutil does not raise for this: it returns `{}`.
3. Every real supervisor on macOS runs under a platform binary, because the
   session script's interpreter is `/bin/zsh`, or `/bin/bash` as the fallback.
4. With no `PATH` in `{}`, the gate refused every supervisor.

Measured on macOS 27 with psutil 7.2.2 by spawning each program with
`PATH=/tmp/x FOO=1`:

| Program | `environ()` keys |
| --- | --- |
| `/bin/bash` | none |
| `/bin/sleep` | none |
| Homebrew `bash` | `FOO`, `PATH` |
| a venv `python` | `FOO`, `PATH`, plus two set by macOS |

`ps -E` reads the same kernel record and shows nothing either.

## Fix

When the supervisor's environment has no `PATH` on macOS, the gate rebuilds the
pane's environment from tmux instead: `show-environment -g` (the server
environment), overlaid by `show-environment -t <session>`, with tmux's `-NAME`
removal marks applied. The launcher passes the pane's `PATH` through
`new-session -e PATH=...` (`build_tmux_env_flags`), so tmux holds the same value
the supervisor started with.

The fallback is limited to macOS. On Linux, procfs reports a platform binary's
environment like any other, so an empty environment there is real, and the gate
still refuses it. If tmux has no `PATH` either, the gate refuses as before.

### Decision record

The issue asked how the gate should get a supervisor's `PATH` on macOS. The
options considered:

- **tmux pane environment (chosen).** It is readable whatever binary the pane
  runs, and it is the channel the launcher already uses to set the pane's `PATH`.
- **The agent process's environment.** That one is readable, but the child body
  shell sits between it and the supervisor and may change variables, so it is not
  the supervisor's environment.
- **Have the supervisor record its `PATH` at start.** The gate exists for
  supervisors started from older scripts, which would never write that record.

## Regression tests

- `test_given_macos_withholds_supervisor_environment_when_probed_then_tmux_pane_environment_decides`:
  with an empty supervisor environment on macOS, the probe runs on the tmux
  session `PATH`. When tmux has no `PATH` of its own, the gate still refuses.
- `test_given_linux_supervisor_with_empty_environment_when_probed_then_tmux_is_not_consulted`.
- The fake `tmux` in the supervisor fixture now answers `show-environment -g` with
  its environment, as real tmux does. That makes the four end-to-end cases pass
  on macOS.
