---
title: "[AI-CLI-maze, AI-CLI-mmgm4, AI-CLI-zzmbn] Session-exit tests fail on a loaded host, and a stop press can wait out the restart window"
category: bugs
tags: [bug, session, interrupt, flaky-test, restart-window]
status: fix-deployed
severity: P2
task: AI-CLI-maze
---

# [AI-CLI-maze, AI-CLI-mmgm4, AI-CLI-zzmbn] Session-exit tests fail on a loaded host

**Status:** fix-deployed

**Severity:** P2

**Created:** 2026-11-22

**Task:** `AI-CLI-maze`, `AI-CLI-mmgm4`, `AI-CLI-zzmbn`

## Symptoms

Tests in `tests/test_session_interrupt_shell.py` passed alone and failed on a busy
host, most often the zsh variants:

1. `test_given_remote_recovery_shell_when_exited_then_original_shell_survives`: the
   agent exits 1 in a remote session, and the "close this session" prompt never
   appears. The pane shows `Template refresh failed` and, for Codex, a second launch.
2. `test_given_running_engine_when_terminal_interrupt_exits_then_original_shell_returns`
   in its `raw-clean` modes, and
   `test_given_clean_exit_restart_window_when_interrupted_then_original_shell_returns`:
   the second Ctrl+C lands after the restart window and the agent relaunches.
3. `tests/test_session_exit_persistence.py::test_given_failing_mark_when_sequence_succeeds_then_mark_is_bounded_and_exit_is_denied`
   on Linux CI: fewer than two mark attempts ran before the exit-persistence deadline.

## Root cause

Four causes. Three are in the tests and one is in the product.

### 1. The recovery test depended on wall time

The wrapper offers the remote recovery shell only after a failed start: an agent
run under three seconds, measured with `date +%s` around the agent. An exit after
that is a crash after a healthy run, which restarts the agent. The test started
the session, attached a caller and then told the agent to exit, so the measured
run was however long that setup took. Under load it took three seconds or more.

The pane proves which path ran: `Template refresh failed` is printed only after
the quick-failure check has passed over the exit (`src/ai_cli/session_script.py`,
the `_need_reload` block). The refresh fails because the test's fake `ai` answers
`get-version` with nothing. That is a fake, not real host state.

### 2. The restart-window tests raced a three-second window

After a healthy run a clean exit opens a stop window, three seconds by default.
The tests waited for the notice, then pressed Ctrl+C. On a loaded host, polling
the pane and sending the key took longer than the window.

### 3. A stop recorded before the window's wait was ignored until the window ended

The window was a plain `sleep "$_restart_window"`. A press that lands between the
notice appearing and the `sleep` starting is recorded as a stop, but nothing ends
the sleep, so the session waits out the whole window before it stops. With the
default three seconds this is a short delay. With a longer configured window the
user waits the full window after pressing. Widening the window in the tests
exposed it.

### 4. The exit-persistence test fitted two retries into a fixed two-second budget

Each attempt starts two short Python processes, a sequence call and a mark, and
each call had a 0.4 s timeout. `scaled()` stretches these by the host's load
average, which does not see a runner that is slow while idle. When a process start
took longer than the timeout, the sequence call timed out and the mark never ran
twice before the deadline.

## Fix

* The wrapper polls for the recorded stop through the window instead of sleeping
  it out, so a stop ends the window at once.
* The recovery test needs no change on this branch. Main pinned its clock first
  (`f789cf17`), then removed the remote recovery shell and its "close this session"
  prompt (`548f0a32`). Its replacement,
  `test_given_failed_start_when_launch_loop_stops_then_original_shell_returns`,
  carries the same pin (`quick_exit`), so cause 1 is fixed on main.
* The restart-window tests configure `restart_stop_window_seconds` longer than
  their own waits. A press still ends the window at once, and a window waited out
  to its end fails the test.
* The exit-persistence test waits for the second mark attempt under a long
  deadline, then ends the retry loop with SIGTERM. Exit 77 and the deny-boot
  record still prove the exit is denied. The deadline path itself stays covered by
  `test_given_permanent_failure_when_deadline_expires_then_generation_is_denied_without_respawn`.
  Main separately made the attempt counter an atomic append (`13e44690`), so a mark
  killed at its timeout no longer erases the count; this fix keeps that counter.

## Regression tests

Each fails on the unfixed tree on every run, at any load:

* The clean-exit restart-window test presses 4.2 s after the notice, past the
  default window. Measured with a 3.5 s delay on the unfixed tree, 3 of 4 failed;
  the fourth passed because `date +%s` works in whole seconds, which can stretch a
  three-second window to almost four. 4.2 s clears that.
* `test_given_stop_press_while_restart_notice_renders_when_agent_exits_cleanly_then_original_shell_returns`
  holds the notice's render in the fake `ai` and presses during it. Unfixed
  product: 4 of 4 fail.
* The exit-persistence test's sequence call answers after `scaled(0.5)` seconds,
  longer than the old 0.4 s timeout at any load, as a slow process start does.
