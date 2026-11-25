---
title: "On Windows, Ctrl+C cannot leave the ai c restart loop"
category: bugs
tags: [bug, session, windows, psmux, interrupt]
status: fix-in-review
severity: P1
---

# On Windows, Ctrl+C cannot leave the ai c restart loop

**Status:** fix-in-review

**Severity:** P1

**Created:** 2026-11-23

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Rejected Hypotheses](#rejected-hypotheses)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

In an `ai c` session on Windows, pressing Ctrl+C several times did not end the session.
Claude Code ended, but the pane stayed open with no prompt and no restart, and further
presses did nothing visible. The only way out was the tmux prefix and `d` (detach).

The launch log showed the wrapper receiving the presses ("Ctrl+C again within 3s to exit",
several times). The wrapper's stop marker `session-int-exit-<session>` had been written and
was never removed, and no `session-final-exit-<session>` file was ever written: the
recorded stop was never acted on.

## Environment

Windows 11, Git Bash (MSYS2) as the session shell, psmux 3.4.0 as `tmux`, Claude Code's
native `claude.exe`. Linux and macOS are not affected; their wrapper path is unchanged.

## Reproduction Steps

`tests/test_windows_psmux_interrupt.py` reproduces it without Claude Code. It renders the
wrapper as a Windows launcher does, starts it in a private psmux server, and runs a
stand-in agent that behaves like Claude Code at this boundary: raw console input (so a lone
Ctrl+C is a key it reads), a double Ctrl+C that exits 0, and a shutdown of a few seconds
that ignores console control events. The test then presses Ctrl+C every half second, as a
user leaving the session does.

Before the fix the pane was still open after four minutes of presses, with the stop marker
left behind. Measured three times by hand and once more under pytest.

## Root Cause Analysis

Two causes, each enough to keep the pane open.

1. **`wait` on the backgrounded native agent never returned.** The child body ran the agent
   as a background job (`"$@" <tty &`) and looped on `wait "$active_agent_pid"`, which is
   what lets the INT trap run on POSIX while the agent holds the terminal. Under Git Bash,
   once Ctrl+C presses arrived while the agent was exiting, that `wait` neither returned for
   the agent's exit nor for the trapped signals. Markers in the wrapper showed it entering
   the wait once, the agent exiting 9 s later, the INT trap running 26 more times, and the
   wait never returning. The process table then held only the child body and its watcher
   subshell: the agent was gone. With the wait replaced by a `kill -0` poll the agent's
   exit was seen 56 s late.

2. **The double-press rule could not be met.** Each press that reaches the wrapper on
   Windows waits for the native helper running at that moment (launch diagnostics, the
   renderer, the lifecycle helper) to finish. Each is a process start of several seconds on
   a loaded host, so presses arrived coalesced and seconds apart, and every one counted as
   a first press ("Ctrl+C again within 3s to exit") again. With cause 1 fixed alone, the
   loop still ran for four minutes of presses under pytest without stopping.

Why only Windows: Claude Code reads the console raw, so its own Ctrl+C handling consumes a
lone press. When it exits it restores the console mode, and every later press becomes a
console control event delivered to every process on the console: the wrapper shells and
whichever native helper is running.

## Rejected Hypotheses

Each was measured in a private psmux server.

- **psmux does not pass Ctrl+C through.** Rejected: `send-keys C-c` reached a Git Bash INT
  trap during `sleep` and `wait`, and reached a native Python child as KeyboardInterrupt.
- **The agent leaves the console in raw mode.** Rejected: after a native child switched off
  processed input and exited, the mode read back 0xe7 and Ctrl+C reached the shell.
- **`kill -TERM` from the trap cannot end a native agent.** Rejected: it ended a native
  process that handles every console control event within 2 s (status 143).
- **`claude` is a launcher whose real process survives.** Rejected: `claude.exe` is one
  process whose parent is the shell.
- **Forks hang while Ctrl+C events arrive.** Rejected: 400 forks under 20 presses ran at the
  same rate as with no presses.
- **A nested `wait` in the trap, a background job started by the trap, or a long-lived
  sibling job blocks the main `wait`.** Rejected: none of them reproduced the hang alone.

## Fix

Windows only (`_native_windows_launcher()`); POSIX output is unchanged.

- The agent runs as the child body's foreground command (`WINDOWS_AGENT_RUN`), as the child
  body already runs under the supervisor there. No pid is needed: the console delivers
  Ctrl+C to the agent directly, and bash runs the INT trap once the agent returns.
- The child body's INT trap records the stop on any press (`WINDOWS_CHILD_INT`), with
  builtins only. A press reaches the wrapper only while Claude Code exits or after it is
  gone, so it is always a request to stop; a lone Ctrl+C inside a running Claude Code is
  still a key only Claude Code reads.
- `session_restart._compatible` rebuilds the previous Windows form in the trusted template,
  so `ai --restart-session` still certifies a session started before this change.

## Verification

- `tests/test_windows_psmux_interrupt.py`:
  - `test_given_running_agent_when_ctrl_c_is_pressed_repeatedly_then_agent_and_restart_loop_end`:
    RED before the fix (the session still alive after 240 s of presses, stop marker left),
    GREEN after (agent exits 0 once, session gone, markers removed).
  - `test_given_running_agent_when_ctrl_c_is_pressed_once_then_agent_reads_it_and_keeps_running`:
    one press is read by the agent and nothing stops. Passes before and after.
- `tests/test_restart_run_state.py`: a Windows program in the previous form is compatible,
  and one with an edited agent launch is rejected.

## Lessons Learned

On Windows a terminal signal is not delivered to one foreground process group. Every
process on the console receives it, including short-lived helpers, and a Git Bash shell
receives it only after its foreground native command returns. Timing rules built on POSIX
signal delivery, such as a 3 s double press, do not carry over, and neither does job
control around native programs.
