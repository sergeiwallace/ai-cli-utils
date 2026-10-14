# Bare-Path Pty Submission — Implementation Plan

**Status:** IMPLEMENTED (awaiting review)
**Created:** 2026-10-14
**Task:** AI-CLI-0nvi

## Founding Ask Coverage

The ask, verbatim:

> no, tmux isn't a hard requirement. can't we use an option to do bare path? and what is the pty on
> bare paths? what does this mean? and if pty is needed for bare cc sessions so that we can do the
> auto-compact arming prompt injection mechanism, then always require either tmux or pty. but we
> need it so that ai c cc sessions can use that prompt injection auto-compact mechanism either with
> tmux or in bare cc session. if pty is how we do that, then do that. whatever is appropriate /
> necessary to enable this.

| Ask fragment | Where it is answered |
|---|---|
| tmux isn't a hard requirement | Scope: tmux launches are unchanged; the bare paths stay |
| what is the pty on bare paths | [Overview](#overview) |
| always require either tmux or pty | Already shipped as the launch-time refusal in `compact_transport.resolve()`; AC-7 keeps it |
| the injection works in a bare cc session | AC-1 to AC-3 |
| whatever is necessary to enable this | AC-4 to AC-6: the pty proxy is complete enough to run a real session |

## Overview

A bare launch (`-b/--bare`, `[session] use_tmux = false`, or tmux unusable) used to replace the
launcher with the agent through `execvp`. The agent then owned the operator's terminal directly,
and nothing else could ever type into it: a running process's controlling terminal cannot be moved
into one we own, and `TIOCSTI` is privileged.

"The pty on bare paths" means the launcher allocates a pseudo-terminal, starts the agent on its
slave side, and stays alive as a thin proxy between the operator's terminal and the pty master.
Writing to the master is an unprivileged way to deliver input the agent reads exactly as if it had
been typed. The launcher exposes that write path as a per-session FIFO, so the auto-compact prompt
can be submitted without tmux.

The pty carries the submission; it does not host the session's UI. There is no screen model, no
scrollback, no detach/reattach and no multiplexing. Those are what tmux is for, and tmux stays the
default and preferred transport.

## Behaviour of the proxy

| Event | What happens |
|---|---|
| Keystroke | The operator's terminal is put in raw mode; every byte is copied to the master unchanged |
| Agent output | Every byte from the master is copied to the operator's stdout unchanged |
| Resize | `SIGWINCH` on the launcher reads the operator's window size and sets it on the pty (`TIOCSWINSZ`); the kernel then delivers `SIGWINCH` to the agent |
| Ctrl-C | Raw mode means the operator's terminal raises no `SIGINT`. The `0x03` byte reaches the pty, whose own line discipline (as the agent configured it) either raises `SIGINT` in the agent or hands the agent the byte. The launcher is never interrupted |
| `SIGTERM`, `SIGHUP` | Forwarded to the agent by `transport._terminate_after_child()`; the proxy keeps relaying until the agent exits, then restores the terminal and exits `128 + signum` |
| Agent exits | Remaining output is drained, the terminal is restored, the FIFO is removed, and the launcher exits with the agent's status (`128 + signum` if it was killed by a signal) |
| Submission | Bytes written to the FIFO are copied to the master, so the agent reads them as typed input |

## Acceptance Criteria

- **AC-1** WHEN a bare `ai c` launch resolves the compact transport to the harness-owned pty, the
  launcher SHALL run the agent on a pty it owns rather than replacing itself with `execvp`.
- **AC-2** WHILE such a session runs, bytes written to its submission FIFO SHALL be read by the
  agent from its terminal. The test asserts what the agent READ, not what was written.
- **AC-3** IF a bare launch has no pty transport (a platform with no pty API), THEN there SHALL be no
  submission FIFO and an injection SHALL NOT reach the agent (the negative control).
- **AC-4** WHILE the session runs, operator keystrokes, including Ctrl-C (`0x03`), SHALL reach the
  agent unchanged, and the agent's output SHALL reach the operator's screen.
- **AC-5** WHEN the operator's terminal is resized, the agent SHALL observe the new size.
- **AC-6** WHEN the agent exits, or the launcher receives `SIGTERM` or `SIGHUP`, the launcher SHALL
  exit with the agent's status (or `128 + signum`), restore the operator's terminal attributes and
  modes, and remove the FIFO.
- **AC-7** WHEN tmux hosts the session, the launch SHALL be unchanged: no pty is allocated and no
  FIFO is created.
- **AC-8** WHILE on a platform without `os.openpty` (Windows), a bare launch SHALL keep its current
  `execvp` behaviour. The pty code is POSIX-only and imported only on the pty path.
- **AC-9** The proxy SHALL NOT use `TIOCSTI`, and SHALL NOT implement scrollback, detach/reattach or
  multiplexing; the module documents this.

## Tests

| AC | Test (all through `ai_cli.main.cli()` with `sys.argv`, on a real pty) |
|---|---|
| AC-1, AC-2 | `tests/test_bare_pty_host.py::test_given_a_bare_launch_when_text_is_written_to_the_submission_fifo_then_the_agent_reads_it` |
| AC-3 | `tests/test_bare_pty_host.py::test_given_no_pty_api_when_a_bare_launch_runs_then_no_submission_fifo_exists` |
| AC-4 | `tests/test_bare_pty_host.py::test_given_a_bare_pty_session_when_the_operator_types_then_keys_and_ctrl_c_reach_the_agent_unchanged` |
| AC-5 | `tests/test_bare_pty_host.py::test_given_a_bare_pty_session_when_the_terminal_is_resized_then_the_agent_sees_the_new_size` |
| AC-6 | `tests/test_bare_pty_host.py::test_given_a_bare_pty_session_when_the_agent_exits_then_status_and_terminal_are_restored`, `tests/test_bare_pty_host.py::test_given_a_bare_pty_session_when_the_launcher_gets_a_termination_signal_then_it_exits_128_plus_signum_restored` (SIGTERM, SIGHUP) |
| AC-7 | `tests/test_bare_pty_host.py::test_given_tmux_hosts_the_session_when_launched_then_no_pty_is_hosted` |
| AC-8 | covered by AC-3's test, which drives the no-pty-API resolution to the `execvp` path |
| AC-9 | No dedicated test: the AC-2 test proves delivery through a plain master write, and the scope is stated in `src/ai_cli/pty_host.py`'s module docstring |

## Decisions

### D-1: The submission endpoint is a per-session FIFO

The decision settles that the pty carries the submission; it does not say how a submitter reaches
the master. Options considered:

- **(a) A named FIFO** at `$XDG_STATE_HOME/ai-cli-utils/pty/<session>.in`, mode `0600`, its path also
  exported to the agent as `AI_CLI_SUBMIT_FIFO`. Any process of the same user can write a line to it
  with no client library. Write-only, which matches "carry the submission, not host the UI".
- **(b) A Unix-domain socket.** Bidirectional, so it could later return screen state, but it needs a
  client and a protocol, and returning screen state is the screen model this scope excludes.
- **(c) Inherited file descriptor.** Only works for a submitter the launcher itself starts, which the
  supervisor is not.

Chosen: (a). It is the smallest thing that delivers the ask and is easy to replace. The path is
derived from the session name, so a supervisor that knows the session can find it without reading
any record.

**Consequence, stated rather than hidden:** a tmux submitter verifies landing by capturing the pane.
A FIFO gives a submitter no screen to capture. A supervisor driving a bare session submits blind
and must confirm the compaction from the transcript instead.

## Out of scope

- The supervisor-side transport that writes to the FIFO lives outside this package.
- A Windows pseudo-console (ConPTY) transport, tracked separately as AI-CLI-ysht.
- Any terminal-UX kinks of running a session through the proxy; they are fixed when a bare session
  is actually used, per the decision.
