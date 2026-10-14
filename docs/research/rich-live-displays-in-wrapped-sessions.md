---
title: "Rich Live Displays in tmux-Wrapped and Remote Launcher Sessions"
category: research
tags: [research, rich, live-display, terminal, tmux, ssh, signals, cli-ux]
status: complete
source: "claude-opus-5-5-2026-10-13"
template_version: "research-1.2.0"
related_docs:
  - docs/designs/launcher-output-standard.md
  - docs/research/launch-sequence-progress-logging-cli-ux.md
---

# Rich Live Displays in tmux-Wrapped and Remote Launcher Sessions

## Founding Ask Coverage

`canonical_root_json`: N/A. `founding_ask_ref_sha256`: N/A. No canonical founding-ask root was
supplied with this task. The maintainer's ask reached this research through the task brief for
AI-CLI-g013 and is quoted verbatim below.

> also, i want live displays. why would that be an issue for claude code? can you /delegate opus
> /research-doc and/or /delegate opus /bug-fix those issues and bugs to figure out how we can enable
> live displays for our tmux process-wrapped cc sessions? i want that.

The brief also records the maintainer's decision on D-1 of
[launcher-output-standard.md](../designs/launcher-output-standard.md): structlog builds the record
and Rich renders it, and live displays are wanted. That decision lifts the design's live-display ban.

Coverage index (non-authoritative; the quoted ask governs):

| Part of the ask | Where it is answered |
|---|---|
| "i want live displays" | [Recommendation](#recommendation), [5. The rule that replaces the ban](#5-the-rule-that-replaces-the-ban) |
| "why would that be an issue for claude code?" | [1. Why Claude Code itself is not the problem](#1-why-claude-code-itself-is-not-the-problem) |
| "those issues and bugs" | [3. Hazards, measured](#3-hazards-measured), [6. Bugs found](#6-bugs-found) |
| "how we can enable live displays for our tmux process-wrapped cc sessions" | [4. Safe pattern per display kind and path](#4-safe-pattern-per-display-kind-and-path) |
| D-1 lifts the ban | [5. The rule that replaces the ban](#5-the-rule-that-replaces-the-ban) |

One conflict between the records is stated here so nobody has to rediscover it. The design on `main`
(base `371e86d`) still carries the ban as enforcement rule 3 and as a T-2 acceptance criterion. Open
PR #301 (AI-CLI-8h3u, head `7477867`) already records D-1 as decided and names this research as the
source of the live-display rules for T-2 and T-6. The later record governs, so this doc assumes the
ban is lifted and supplies the rule that replaces it.

**Status:** complete

**Created:** 2026-10-13

**Task:** AI-CLI-g013

**Related docs:**

- `docs/designs/launcher-output-standard.md`
- `docs/research/launch-sequence-progress-logging-cli-ux.md`

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [1. Why Claude Code itself is not the problem](#1-why-claude-code-itself-is-not-the-problem)
- [2. What a Rich live display does to a terminal](#2-what-a-rich-live-display-does-to-a-terminal)
- [3. Hazards, measured](#3-hazards-measured)
  - [H1. Exec while a display is live](#h1-exec-while-a-display-is-live)
  - [H2. A crash outside the context manager](#h2-a-crash-outside-the-context-manager)
  - [H3. SIGTERM and SIGHUP](#h3-sigterm-and-sighup)
  - [H4. SIGKILL](#h4-sigkill)
  - [H5. The alternate screen](#h5-the-alternate-screen)
  - [H6. A second writer on the same terminal](#h6-a-second-writer-on-the-same-terminal)
  - [H7. Raw mode during an ssh attempt](#h7-raw-mode-during-an-ssh-attempt)
  - [H8. Background process groups and SIGTTOU](#h8-background-process-groups-and-sigttou)
  - [H9. Rich's stdout and stderr redirection](#h9-richs-stdout-and-stderr-redirection)
  - [H10. Non-terminal output and the environment overrides](#h10-non-terminal-output-and-the-environment-overrides)
  - [H11. A stale COLUMNS](#h11-a-stale-columns)
  - [Negative findings](#negative-findings)
- [4. Safe pattern per display kind and path](#4-safe-pattern-per-display-kind-and-path)
  - [Local tmux-wrapped path](#local-tmux-wrapped-path)
  - [Remote path](#remote-path)
  - [Inside the tmux pane](#inside-the-tmux-pane)
- [5. The rule that replaces the ban](#5-the-rule-that-replaces-the-ban)
  - [The owner](#the-owner)
  - [Enforcement](#enforcement)
- [6. Bugs found](#6-bugs-found)
- [Gaps, blindspots and emergent findings](#gaps-blindspots-and-emergent-findings)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Appendix: Spike harness](#appendix-spike-harness)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

<!-- doc:region name="context" kind="immutable" -->

## Context

The `ai` launcher prints launch phases, then hands the terminal to tmux with `os.execvp`. In tmux
a generated session script supervises Claude Code (or another agent). The remote path `ai c -R`
keeps a Python parent alive and runs ssh in a reconnect loop. The maintainer wants Rich live
displays (Live, Progress, Status spinners, an updating status region) in this launcher. The question
is how to run them without leaving the terminal broken on any exit path.

**Primary period:** 2025–2026 (Rich 15.0.0, tmux 3.7c, the repository at `371e86d`)
**Source weighting:** measurements on this codebase first, then Rich's own docs and source, then
POSIX and tool manuals. Terminal semantics (POSIX, tmux, OpenSSH) are foundational and cited
regardless of age.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

Rich 15.0.0 (the version the design measured) is the subject, read at its installed source. The
codebase is `main` at `371e86d`, plus the open line-discipline PR #301 where it changes the exec
boundary. Spikes ran on 2026-10-13 on an Apple Silicon Mac (macOS, Darwin 27.0.0), Python 3.14,
inside a private tmux 3.7c server so that pane state can be read back with tmux format variables.
No Linux or Windows run was made; [Gaps](#gaps-blindspots-and-emergent-findings) says what that
leaves open.

Claim tags follow the research template. A spike result is cited as `S-n` and its reproduction is in
[Appendix: Spike harness](#appendix-spike-harness). The scratch scripts themselves were not
committed, so the appendix carries everything needed to rerun them.

## Executive Summary

Live displays are safe here under one rule: every live region is entered through a single owner in
the output module, and the owner is torn down before anything else can own the terminal. That means
before every exec, before every ssh or mosh attempt, before any child that inherits the terminal, on
SIGTERM and SIGHUP, and at exit. Claude Code is never the problem. The problems are the exec
boundary, which skips all cleanup, and a second writer on the same terminal. Measured on this
codebase, a Live left running across an exec, a SIGTERM or a SIGKILL leaves a hidden cursor and an
open row. A Live during an ssh attempt staircases, because ssh clears `OPOST`. A Live while another
process writes duplicates its frames into scrollback. Rich never touches termios. The owner
prototype (S-20) left a clean terminal on exec, SIGTERM and SIGHUP. With cursor hiding turned off
(S-21), even SIGKILL left only an open row. Two real bugs surfaced on the way. SIGTERM or SIGHUP to
`ai c -R` skips the reconnect runner's cleanup today (S-22). A naive signal handler would make that
worse by SIGKILLing ssh (S-23).

## 1. Why Claude Code itself is not the problem

Claude Code draws its own interface inside a tmux pane. The launcher's live display would run
earlier, in the outer terminal, before `os.execvp("tmux", ...)`. After the exec the launcher process
no longer exists, so it cannot draw over Claude Code. [INFERENCE]

The trouble sits at three boundaries that Claude Code happens to be on the far side of.

The first is the exec handoff. `os.execvp` replaces the process immediately and runs no `finally`,
no `atexit` and no context-manager exit. Python's docs say open file objects are not flushed either
[VERIFIABLE][^6]. A Live still running at that moment never restores the cursor and never closes its
row (S-1). tmux repairs the cursor when its client exits, but the stale frame stays in the outer
scrollback, and tmux's own exit message lands on that row (S-2).

The second is the shared pane. Inside tmux, the supervisor, the child shell and the agent all write
to one pane. Any Live that kept refreshing while the agent owned the pane would draw into the
agent's screen (S-9 shows two writers corrupting each other). If the writer were in a background
process group and `TOSTOP` were set, it would be stopped by SIGTTOU instead (S-15). Neither can
happen if no Live spans the agent's run.

The third is Claude Code as a caller. Agents run `ai` commands from a tool shell whose output is a
pipe. Rich then draws nothing live, and a `transient=True` display prints nothing at all (S-11). A
progress record must still reach a pipe as an ordinary line.

## 2. What a Rich live display does to a terminal

`Live.start()` hides the cursor, replaces `sys.stdout` and `sys.stderr` with proxies, registers a
render hook, and starts a refresh thread that is a daemon [VERIFIABLE][^4]. Each refresh moves to
column 0, erases the line, then moves up and erases once per extra row of the last frame, and
prints the new frame (Rich `live_render.py`, read at 15.0.0). `Live.stop()` stops the thread,
prints the final frame (or erases it when `transient=True`), restores the streams, shows the cursor
and leaves the alternate screen if it entered one [VERIFIABLE][^4]. `Status` is a `Live` with
`transient=True`, and `Progress` wraps a `Live` with `redirect_stdout` and `redirect_stderr` both on
by default [VERIFIABLE][^3].

Rich installs no signal handler and no `atexit` hook. A grep of every module in the installed
`rich` package finds no `import signal`, no `import atexit` and no `signal.signal` call. Rich's
maintainer gave the same answer when a user reported a cursor left hidden after Ctrl+C: use the
context manager, which restores the cursor [VERIFIABLE][^5]. Teardown therefore happens only when
`stop()` runs, which means only on a normal return or on an exception unwinding through a `with`
block.

The only terminal modes Rich ever sets are cursor visibility (`?25`) and the alternate screen
(`?1049`) (Rich `control.py`, read at 15.0.0). It never calls `tcsetattr`, and `stty -a` was
identical before and after every spike, including SIGKILL (S-19).

## 3. Hazards, measured

| # | Hazard | Real here? | Evidence |
|---|---|---|---|
| H1 | Exec while live | Yes: 14 `os.execvp` handoff sites | S-1, S-2 |
| H2 | Crash outside `with` | Only if code calls `start()` directly | S-3, S-4 |
| H3 | SIGTERM, SIGHUP | Yes: no handler anywhere in the launcher | S-6, S-22 |
| H4 | SIGKILL | Yes, unavoidable; residue can be cut to one open row | S-7, S-21 |
| H5 | Alternate screen | Only if `screen=True` is used | S-8 |
| H6 | Second writer | Yes: children, heartbeat thread, stderr tee | S-9 |
| H7 | Raw mode (ssh) | Yes, during every `-R` attempt | S-10 |
| H8 | Background group | Yes, in the pane once the agent is foreground | S-15 |
| H9 | Stream redirection | Yes, if left at Rich's default | S-14 |
| H10 | Non-TTY and env overrides | Yes: agent tool shells, logs, `FORCE_COLOR` | S-11 to S-13 |
| H11 | Stale `COLUMNS` | Possible; not observed in this environment | S-16 |

### H1. Exec while a display is live

Mechanism: `os.execvp` replaces the process without running cleanup or flushing buffers
[VERIFIABLE][^6]. The launcher has 14 `os.execvp` handoff sites in `src/ai_cli/main.py` at
`371e86d` (for example `main.py:239`, `main.py:3800`, `main.py:4659`). PR #301 routes all of them
through one `_exec_handoff()` that calls `flush_sinks()` first.

Measured (S-1): a Live with redirection on, then `os.execvp` into a shell. Afterwards the pane's
`cursor_flag` was 0 and stayed 0 at the next prompt. The exec'd child's first line joined the stale
frame on one row (`live frame: exec testEXECED-CHILD`). A partial line written to the redirected
`sys.stdout` never appeared, because Rich's stdout proxy buffers until a newline.

Measured (S-2): a `Status` spinner, then exec into a nested tmux client. When that client exited,
`cursor_flag` was back to 1, so tmux restored the cursor. The spinner frame stayed in scrollback with
tmux's `[exited]` on the same row. This is the AI-CLI-8h3u run-together class, produced by a
renderer.

### H2. A crash outside the context manager

Mechanism: an exception unwinding through `with Live(...)` runs `__exit__`, which calls `stop()`. A
display started with `.start()` and no `with` has no unwinding hook.

Measured: an uncaught `RuntimeError` inside `with` left a clean pane, cursor visible (S-3). The same
error after `.start()` left the cursor hidden and the frame row open (S-4). Its traceback also went
through Rich's stderr proxy, which re-wrapped the long file path onto a second line.

### H3. SIGTERM and SIGHUP

Mechanism: Python translates only SIGINT into an exception (`KeyboardInterrupt`); SIGTERM and
SIGHUP keep their default action, which ends the process without unwinding [VERIFIABLE][^7]. A
grep of `src/ai_cli/` at `371e86d` finds no `signal.signal` call for either signal.

Measured: SIGINT during a Live unwound cleanly (S-5). SIGTERM left `cursor_flag` 0 and the frame row
open, with the shell's next output joined to it (S-6). The same mechanism skips the reconnect
runner's `finally` on the real code ([B-1](#6-bugs-found), S-22).

### H4. SIGKILL

Mechanism: SIGKILL cannot be caught, blocked or ignored [VERIFIABLE][^7], so no handler can tear a
display down. The only choices are to leave less behind and to let the next writer repair it.

Measured: SIGKILL left `cursor_flag` 0 and the frame row open (S-7). With cursor hiding disabled
through a `Console` subclass whose `show_cursor` does nothing, SIGKILL left the cursor visible and
only an open row (S-21). The next prompt's PROMPT_SP (zsh) or the L6 line-start fence in PR #301
handles an open row. Both repair paths already restore the cursor too: the session script's
`_reset_agent_terminal` writes `?25h` (`session_script.py:666-672`), and so does the transport's
`_TERMINAL_RESTORE` (`transport.py:42-57`).

### H5. The alternate screen

Mechanism: `screen=True` sends `?1049h` and relies on `stop()` to send `?1049l`.

Measured (S-8): SIGKILL with `screen=True` left the pane in the alternate screen (`alternate_on=1`),
with the cursor hidden and the user's scrollback out of view. A launcher has no full-screen view to
justify this, so the owner should never use `screen=True`. [INFERENCE]

### H6. A second writer on the same terminal

Mechanism: a Live redraws by counting the rows of its last frame and moving up that many. Any byte
another writer puts on the terminal moves the cursor without Rich knowing. The live frame then
redraws in the wrong place. Rich routes only writes that pass through its own console or its
stream proxies; a child process writes straight to the file descriptor. An open Rich issue reports
the same conflict between Progress and streamed text [VERIFIABLE][^12].

Measured (S-9): a `Progress` whose loop ran a child that wrote to fd 2. Each child line was joined to
a stale bar, leaving three duplicate bars in scrollback.

Real here: `run_relayed` children capture their output and are safe. Children that inherit the
terminal are not: the ssh attempt, `direnv`, prompts such as `sudo -v`, and anything run with
`subprocess.run` and no capture. So are the launch reporter's heartbeat timer thread
(`launch_reporter.py:74-90`) and the ssh stderr tee thread (`transport.py:98-146`), if they write
straight to the stream while a Live is up. The owner prototype printed records from five threads
through the live console without corruption (S-20).

### H7. Raw mode during an ssh attempt

Mechanism: OpenSSH's `enter_raw_mode()` clears `OPOST` on the local terminal [VERIFIABLE][^9].
With output post-processing off, characters are sent unchanged [VERIFIABLE][^8], so a `\n` moves
down a row without returning to column 0. Rich ends frame lines with `\n`.

Measured (S-10): a two-row Progress under `stty -opost` drew 13 stale rows in a staircase, and the
shell prompt landed at a stray column. The same Progress rendered cleanly when run on an inner pty
that kept `OPOST`, inside an outer terminal held raw by `script(1)` (S-18). That is the `ssh -t`
topology, so a Live run by the remote-side `ai` is safe.

### H8. Background process groups and SIGTTOU

Mechanism: a background process group that writes to its controlling terminal while `TOSTOP` is set
is sent SIGTTOU. Calls that set terminal parameters, `tcsetattr` and `tcsetpgrp` among them, are
treated as if `TOSTOP` were always set [VERIFIABLE][^8]. The supervisor already handles the second
half: it ignores SIGTTOU around `tcsetpgrp` (`session_script.py:52-54`, `session_script.py:466-470`).

Measured (S-15): the pane's default is `-tostop`, and a background Live's writes went through
there. After `stty tostop` the same job was `suspended (tty output)`.

Real here: once the supervisor promotes the child's group, the supervisor and any process it starts
are in the background. A Live there would either draw over the agent (`-tostop`) or stop (`tostop`).
Rich itself never calls `tcsetattr` (S-19), so it cannot trigger the SIGTTOU-on-`tcsetattr` case.
The owner should start a region only when `os.tcgetpgrp(fd) == os.getpgrp()`, the check PR #301
adds as `_owns_foreground()`.

### H9. Rich's stdout and stderr redirection

Mechanism: with redirection on, Rich replaces `sys.stderr` with a proxy that splits on newlines,
decodes ANSI into styled text and prints it above the live frame (Rich `file_proxy.py`, read at
15.0.0).

Measured (S-14): through the proxy, `\x1b[?25h` and `\x1b[?1049l` were dropped. For `abc\r` followed
by text, only the text after the CR survived. An in-place progress line written with a CR and no
newline was held back until `stop()`. On this codebase, `restore_terminal()` writes its mode resets
with `_out.raw(...)` to `sys.stderr` (`transport.py:60-76`). Under a redirected Live those resets
would be swallowed without any error. The owner should set `redirect_stdout=False` and
`redirect_stderr=False`, and send records through the live console explicitly.

### H10. Non-terminal output and the environment overrides

Mechanism: Rich decides "terminal" from `isatty()` unless `TTY_COMPATIBLE` or `FORCE_COLOR` says
otherwise, and decides "interactive" (animated) from terminal plus `TERM` not being `dumb` or
`unknown`, unless `TTY_INTERACTIVE` says otherwise [VERIFIABLE][^2]. `NO_COLOR` removes colour only
[VERIFIABLE][^2], which matches the NO_COLOR convention's own scope [VERIFIABLE][^11].

Measured:

| Spike | Setting | Result |
|---|---|---|
| S-11 | stderr is a file, `transient=True` | 1 byte written: the record is lost |
| S-11 | stderr is a file, persistent | one final frame, no escape bytes |
| S-12 | stderr is a file, `FORCE_COLOR=1` | 63 escape sequences, cursor hide, CR redraws, all in the file |
| S-12 | `FORCE_COLOR=1` plus `TTY_COMPATIBLE=0` | plain final frame again |
| S-13 | real pty, `NO_COLOR=1` | colour gone; animation and cursor hiding remain |
| S-13 | real pty, `TERM=dumb` | no animation, no escapes |
| S-13 | real pty, `TTY_INTERACTIVE=0` | no animation, but the cursor hide and show are still written |

Real here: the package already owns this decision in `wants_color()` (`output.py:132-141`). The
owner should pass its own answer to Rich as `force_terminal` and `force_interactive`, so a stray
`FORCE_COLOR` cannot put animation into a log. It should also keep the record path separate from
the display, so a non-interactive run emits each progress record as a plain line.

### H11. A stale COLUMNS

Mechanism: Rich takes the width from the `COLUMNS` environment variable when it is set, ahead of the
terminal's real size [VERIFIABLE][^2]. A frame wider than the terminal wraps, the row count Rich
uses to move back up is too small, and every refresh leaves rows behind.

Measured (S-16): `COLUMNS=160` in a 100-column pane, with a two-row display whose first row was 140
characters. 20 duplicate rows were left in scrollback.

Real here: not observed. `COLUMNS` was not exported in the tool shell, and the live tmux server's
global environment had no `COLUMNS`. The risk is real for a user who exports it, and a tmux server
copies its starting environment into the global environment [VERIFIABLE][^10]. The owner should
pass `width` from `os.get_terminal_size(fd)` and re-read it on SIGWINCH, and keep the region to one
cropped line.

### Negative findings

- Rich never changed termios in any spike, SIGKILL included (S-19).
- Narrowing the pane mid-render under tmux left a clean final frame after tmux reflowed it (S-17).
- An exception inside `with Live(...)` and a SIGINT both unwound cleanly (S-3, S-5).

## 4. Safe pattern per display kind and path

Rich offers three display kinds. A `Status` is a spinner with text. A `Progress` is one or more bars
with a total or an indeterminate pulse. A `Live` is any renderable redrawn on a timer. All three
are one `Live` underneath, so one owner covers all of them.

### Local tmux-wrapped path

| Window | Display | Rule |
|---|---|---|
| Launch phases before handoff (update, worktree, dolt_server, direnv, plan) | One-line `Status` showing the current phase and its elapsed seconds | Finished phases print as permanent records above it. The heartbeat's escalation stays in the text, so a stall stays visible. Torn down by `flush_sinks()` in `_exec_handoff()` |
| `ai update` and install steps with a real total | `Progress` with a bar | Paused around any child that inherits the terminal; torn down before the `uv` exec at `main.py:4659` |
| A child that inherits the terminal (`direnv allow`, `sudo -v`, a git credential prompt) | None | Stop the region before the child starts and restart it after |
| After the exec into tmux | None possible | The launcher process is gone |

### Remote path

| Window | Display | Rule |
|---|---|---|
| Local side, before the first attempt | Same as the local launch phases | Torn down before `subprocess.call(ssh_args)` |
| Local side, during an ssh or mosh attempt | Never | ssh holds raw mode (H7) and the stderr tee writes (H6). PR #301 marks this span with `foreign_terminal()`, and entering it must stop any region |
| Local side, reconnect backoff | One-line countdown: attempt n of m, seconds to the next try, time connected | Starts only after `restore_terminal()` and `reclaim_terminal()`, with `OPOST` back on. Stopped before the next attempt |
| Local side, credential wait (up to `auth_wait_seconds`, 4 hours by default) | One-line countdown with the re-authenticate instruction | Same as the backoff; this is the longest idle window in the launcher and the best use of a live line |
| Remote side (`ai c --is-remote` under `ssh -t`) | Same as the local launch phases | Its pty keeps `OPOST`, so it is safe (S-18). Torn down before its own exec into `tmux attach` |

### Inside the tmux pane

The reload display stays as [D-4](../designs/launcher-output-standard.md#d-4) designed it: a
supervisor-owned in-place line with an attempt count, written by a one-shot `ai internal progress`
while the supervisor holds the foreground. No process spans the reloads to host a refresh thread.
The supervisor is a shell, each child is replaced, and the agent owns the pane between reloads (H8).
A Live there would also hide the stall zss5 asks to make louder. The line can still use Rich for
its style, and a one-shot process may draw one frame, but nothing in the pane may keep a refresh
thread running. [INFERENCE]

## 5. The rule that replaces the ban

### The owner

One context manager in the output module, here called `live_region()`, is the only code that may
construct a Rich `Live`, `Progress` or `Status`. A throwaway prototype of it (S-20) behaved as
described below. Its contract:

1. It starts only when the target stream is a TTY, `wants_color()`-style checks pass (`TERM` not
   `dumb`), this process owns the terminal's foreground group, `OPOST` is on, and no
   `foreign_terminal()` span is open. Otherwise it yields `None`, and the caller's progress records
   print as plain lines.
2. It builds its `Console` with `force_terminal` and `force_interactive` set from that decision,
   `width` from the real terminal size, `markup=False` and `highlight=False`. It overrides
   `show_cursor` to do nothing, which leaves the cursor visible during the display, so a SIGKILL
   leaves no hidden cursor (S-21).
3. It constructs the `Live` with `transient=True`, `redirect_stdout=False`, `redirect_stderr=False`
   and never `screen=True`. A finished region leaves nothing behind. Every lasting fact goes out as
   a record.
4. While a region is active, the terminal sink writes records through the region's console. Rich
   then prints them above the live line (Rich docs [VERIFIABLE][^1]). The record still passes
   through PR #301's `inert()` and the one output lock.
5. Teardown is one idempotent function, guarded by the output lock and suppressing `OSError` (a
   hung-up terminal fails writes). Five callers run it: the context manager's exit, `flush_sinks()`
   (so every `_exec_handoff()` and the `atexit` hook), entry into `foreign_terminal()`, the signal
   handlers, and the helper that runs a terminal-inheriting child.
6. On POSIX the launcher installs SIGTERM and SIGHUP handlers once, at startup. Each tears the
   region down, then forwards the signal to any foreground child it is waiting on and unwinds after
   that child exits (B-2 explains why). Where `signal.SIGHUP` does not exist (Windows), only the
   available signals are registered [VERIFIABLE][^7].
7. Nesting is refused. A second `live_region()` while one is active updates the active region's
   text. Rich would otherwise stack them [VERIFIABLE][^1], which makes the region taller than one
   line.

### Enforcement

The design's enforcement rule 3 becomes three tests.

1. An AST scan fails any import of `rich.live`, `rich.progress` or `rich.status`, and any
   `Console.status` call, outside the owner module.
2. A pty test (PR #301 already renders a pty with `pyte`) runs a region and then each exit path:
   `_exec_handoff()`, SIGTERM, SIGHUP, an exception, and `foreign_terminal()` entry. Each run must
   leave the cursor visible, no alternate screen, and the cursor at column 0 of a fresh row.
3. A non-TTY test runs the same producer with stderr as a pipe, and once with `FORCE_COLOR=1`. Each
   run must emit every progress record as a plain line with no escape byte.

## 6. Bugs found

Per the brief, these are notes and are not fixed here.

- B-1. SIGTERM or SIGHUP to `ai c -R` skips the cleanup in `run_ssh_with_reconnect`
  (`transport.py:353-356`). The launcher installs no handler for either signal, so the default
  action ends Python inside `subprocess.call` (`transport.py:270`). Measured on the real function
  with a stand-in ssh that arms SGR mouse tracking (S-22). After SIGTERM and after SIGHUP the pane
  still had `mouse_any_flag=1` and `mouse_sgr_flag=1`, the AI-CLI-w679 symptom. The cleanup command
  (`ai internal cleanup-session-files` in production) never ran, and no outcome record was written.
  The SIGINT control restored everything and ran the cleanup. The mosh loop's `finally`
  (`transport.py:800-807`) has the same gap by inspection, since no handler exists anywhere; it was
  not measured.
- B-2. The obvious fix for B-1 is a handler that raises `SystemExit`, and that fix breaks the
  terminal another way. `subprocess.call` kills its child on any exception, so ssh is SIGKILLed and
  never leaves raw mode. Measured (S-23) with a stand-in that enters raw mode and restores the tty on
  SIGTERM, as ssh's cleanup does. The `SystemExit` handler restored the mouse modes and ran the
  cleanup, but left the terminal at `-icanon -echo -opost`. A handler that forwarded SIGTERM to the
  child and unwound after it exited restored all of it. In both variants the outcome record says
  `error` for a deliberate termination, which reads as a failure.
- B-3. Two hand-maintained lists of terminal resets have drifted. The session script's
  `_reset_agent_terminal` (`session_script.py:666-672`) lacks the autowrap re-enable (`?7h`) and
  the attribute reset (`\033[0m`) that `transport._TERMINAL_RESTORE` sends. It also sends
  `\033[>4m` where the transport sends `\033[>4;0m`. Low severity, and not caused by Rich, which
  touches neither mode.
- B-4. The design on `main` still bans live displays (enforcement rule 3 and a T-2 acceptance
  criterion), while PR #301 records D-1 as decided with live displays wanted. When T-2 lands, rule 3
  needs replacing with the enforcement in [section 5](#enforcement).

## Gaps, blindspots and emergent findings

- No Linux or Windows run. Rich drives the legacy Windows console through Win32 calls and skips the
  alternate screen there (Rich `console.py`, read at 15.0.0). The Windows path has no tmux handoff
  (`main.py:2964-2970` falls back to a bare launch), and remote ssh is refused on Windows. Windows
  therefore needs only the non-TTY test and a console smoke test.
- No real Claude Code run under a live region. The pane-sharing hazard was shown with a stand-in
  writer (S-9). Whether Claude Code sets `TOSTOP` was not checked; the foreground check in the owner
  makes the answer irrelevant.
- No real ssh or mosh host. S-18 and S-23 use `script(1)` and a shell stand-in for ssh's terminal
  handling. ssh's own SIGTERM cleanup was inferred from that stand-in, not read in OpenSSH's source.
- A SIGWINCH during a two-row display, on a terminal that does not reflow the way tmux does, was not
  tested. One cropped row avoids the case.
- Emergent: the reconnect backoff and the credential wait are the best fit for a live display
  in this launcher. The credential wait can last hours, and today it prints one static line per
  retry. The prior research doc argued against Live for serial launch phases
  ([launch-sequence-progress-logging-cli-ux.md](launch-sequence-progress-logging-cli-ux.md)). That
  argument still holds for phase history, which this design keeps as permanent records. It did not
  consider long waits.

## Comparison

| # | Criterion | A. Keep the ban | B. Rich Live through one owner | C. Hand-rolled CR status line | D. Full-screen TUI |
|---|---|---|---|---|---|
| 1 | Gives the maintainer live displays | No | Yes | Partly: no animation without a timer thread | Yes |
| 2 | Residue on SIGKILL | None | One open row (S-21) | One open row | Alternate screen stuck (S-8) |
| 3 | Teardown paths to own | None | One function, five callers | Same as B, hand-written | Same as B, plus screen restore |
| 4 | Fits the exec handoff | Yes | Yes, via `flush_sinks()` | Yes | Poorly: must leave the screen first |
| 5 | Non-TTY degrade | Built in | Plain records by construction | Must be hand-written | Must be hand-written |
| 6 | Code the package maintains | None | One context manager plus tests | A renderer, timer and width logic | A UI layer |

Rich's adoption is already corroborated in the design's D-1 analysis (pip vendors it; Typer requires
it). Option B adds no package beyond D-1's choice.

## Recommendation

Adopt option B. Enter every Rich `Live`, `Progress` or `Status` through one owner in the output
module, and tear it down from `flush_sinks()`, from `foreign_terminal()` entry, from SIGTERM and
SIGHUP handlers, and before any child that inherits the terminal. Use it for the pre-handoff launch
phases, `ai update` progress, the remote reconnect backoff, the credential-wait countdown and the
remote-side launch phases. Never use it during an ssh or mosh attempt or inside the tmux pane, where
the reload line stays D-4's supervisor-owned in-place line. Fix B-1 with the forwarding handler
described in B-2, in its own change, before or with the first live region, because the same
handlers are what tear a region down. Confidence: high on the mechanisms and the owner rule, which
the spikes reproduced; medium on the Windows and real-host behaviour, which was not run.

## Open Questions

1. Should the launch-phase spinner replace the heartbeat lines on a TTY, or run alongside them? This
   doc assumes it replaces them on a TTY while the record keeps the heartbeat for the log file.
2. Does the maintainer want a visible cursor during a display (the SIGKILL-safe choice in owner rule
   2), or Rich's default hidden cursor with the small SIGKILL risk it carries?
3. Should B-1's handler also cover the mosh loop? It shares the gap by inspection but was not
   measured.

## Sources

[^1]: Textualize. (2026). [Live Display](https://rich.readthedocs.io/en/stable/live.html). Rich documentation. Verified accessible (HTTP 200) 2026-10-13. (transient, redirection, printing above the display, nesting.)
[^2]: Textualize. (2026). [Console API](https://rich.readthedocs.io/en/stable/console.html). Rich documentation. Verified accessible (HTTP 200) 2026-10-13. (Environment variables: TERM, FORCE_COLOR, NO_COLOR, TTY_COMPATIBLE, TTY_INTERACTIVE, COLUMNS; force_terminal and force_interactive.)
[^3]: Textualize. (2026). [Progress Display](https://rich.readthedocs.io/en/stable/progress.html). Rich documentation. Verified accessible (HTTP 200) 2026-10-13. (transient, redirect_stdout and redirect_stderr defaults, progress.console.)
[^4]: Textualize. (2026). [rich/live.py](https://github.com/Textualize/rich/blob/master/rich/live.py). GitHub. Verified accessible (HTTP 200) 2026-10-13. (Daemon refresh thread, start and stop sequence; no signal or atexit use.)
[^5]: Textualize. (2025). [Progress bar hides cursor and does not restore it if interrupted, issue #3673](https://github.com/Textualize/rich/issues/3673). GitHub. Verified accessible (HTTP 200) 2026-10-13. (Maintainer's answer: the context manager restores the cursor.)
[^6]: Python Software Foundation. (2026). [os: Process Management, the exec functions](https://docs.python.org/3/library/os.html#os.execvp). Python documentation. Verified accessible (HTTP 200) 2026-10-13. ("The current process is replaced immediately. Open file objects and descriptors are not flushed.")
[^7]: Python Software Foundation. (2026). [signal: Set handlers for asynchronous events](https://docs.python.org/3/library/signal.html). Python documentation. Verified accessible (HTTP 200) 2026-10-13. (Handlers run in the main thread; SIGINT becomes KeyboardInterrupt; SIGKILL cannot be caught; SIGHUP is Unix-only.)
[^8]: The Open Group. (2024). [General Terminal Interface, chapter 11](https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap11.html). POSIX.1-2024 Base Definitions. Verified accessible (HTTP 200) 2026-10-13. (11.1.4 Terminal Access Control: SIGTTOU, TOSTOP, tcsetattr and tcsetpgrp; 11.2.3 OPOST.)
[^9]: OpenSSH. (2026). [sshtty.c](https://github.com/openssh/openssh-portable/blob/master/sshtty.c). GitHub. Verified accessible (HTTP 200) 2026-10-13. (`enter_raw_mode()` clears OPOST, ICANON, ECHO and ISIG.)
[^10]: OpenBSD. (2026). [tmux(1)](https://man.openbsd.org/tmux). OpenBSD manual pages. Verified accessible (HTTP 200) 2026-10-13. (Global environment copied at server start; format variables cursor_flag, alternate_on, mouse_any_flag, mouse_sgr_flag.)
[^11]: NO_COLOR. (2026). [NO_COLOR: disabling ANSI color output by default](https://no-color.org/). no-color.org. Verified accessible (HTTP 200) 2026-10-13. (The convention covers colour only.)
[^12]: Textualize. (2024). [There is no way to ensure that rich.progress.Progress can be implemented at the same time as stream text output, issue #3523](https://github.com/Textualize/rich/issues/3523). GitHub. Verified accessible (HTTP 200) 2026-10-13. (Open report of Progress conflicting with streamed output.)

## Appendix: Spike harness

Every spike ran inside a private tmux server on its own socket, so the pane state could be read back
after the program exited. The pane shell is `zsh -f` with `PS1='$ '`. Rich came from
`uv run --no-project --with rich==15.0.0`. The core of the harness:

```bash
T() { env -u TMUX tmux -L spike -f /dev/null "$@"; }
T new-session -d -s s -x 100 -y 30 -c "$SCRATCH" "zsh -f"
T send-keys -t s "stty -a > before.txt; uv run -q --no-project --with rich==15.0.0 python live_spike.py $MODE; echo EXIT=\$?" Enter
# for signal spikes: wait for the spike to write its pid, then kill -TERM / -HUP / -KILL that one pid
T send-keys -t s "stty -a > after.txt" Enter
T display -p -t s 'cursor_flag=#{cursor_flag} alternate_on=#{alternate_on} mouse_any=#{mouse_any_flag} mouse_sgr=#{mouse_sgr_flag}'
T capture-pane -p -S - -t s
diff before.txt after.txt
```

| Spike | What runs | Observed |
|---|---|---|
| S-1 | `with Live(..., redirect_stdout=True)`: write a partial line to `sys.stdout`, then `os.execvp("sh", ["sh", "-c", "sleep 0.3; echo EXECED-CHILD"])` | `cursor_flag=0` after the prompt; `live frame: exec testEXECED-CHILD` on one row; partial line lost |
| S-2 | `with console.status(...)`, then exec `tmux -L inner new-session "sleep 1"` | `cursor_flag=1` after; `⠼ working[exited]` on one row |
| S-3 | `raise RuntimeError` inside `with console.status(...)` | clean, `cursor_flag=1` |
| S-4 | `status.start()`, then `raise RuntimeError` | `cursor_flag=0`; frame row open; traceback re-wrapped |
| S-5 | SIGINT (tmux `send-keys C-c`) during a status | clean, `EXIT=130` |
| S-6 | SIGTERM to the spike pid during a status | `cursor_flag=0`; `⠸ workingEXIT=143` |
| S-7 | SIGKILL during a status | `cursor_flag=0`; `⠸ workingEXIT=137` |
| S-8 | SIGKILL during `Live(..., screen=True)` | `alternate_on=1`, `cursor_flag=0` |
| S-9 | `Progress` whose loop runs `sh -c 'echo child-stderr-line-N >&2'` | three bars each joined to a child line |
| S-10 | two-task `Progress` after `stty -opost` | 13 stale rows in a staircase; prompt displaced |
| S-11 | two-task `Progress`, stderr redirected to a file, transient and persistent | 1 byte; 147 bytes with no escapes |
| S-12 | as S-11 persistent with `FORCE_COLOR=1`, then also `TTY_COMPATIBLE=0` | 63 escapes and 7 CRs in the file; then none |
| S-13 | `Progress` under `script -q out ...` in the pane, with `NO_COLOR=1`, `TERM=dumb`, `TTY_INTERACTIVE=0` | see the H10 table |
| S-14 | `Console(file=StringIO(), force_terminal=True)` and `Live(redirect_stderr=True)`; write `?25h`, `?1049l`, `abc\r...` and a CR-led partial line to `sys.stderr` | the two mode sequences and the pre-CR text absent from the output; the partial line held until stop |
| S-15 | the spike as a background job (`&`) under `stty -tostop`, then `stty tostop` | completes; then `suspended (tty output)` |
| S-16 | a 2-row `Live` with a 140-character first row, `COLUMNS=160` in a 100-column pane | 20 duplicate rows |
| S-17 | two-task `Progress`, `tmux resize-window -x 45` mid-run | clean after reflow |
| S-18 | `script -q /dev/null sh -c 'stty opost onlcr; ... multiline'` in the pane | clean |
| S-19 | `diff` of `stty -a` before and after, every spike above | identical (S-10's diff is the harness's own `stty -opost`) |
| S-20 | prototype owner (below): exec, five threads printing records, SIGTERM, SIGHUP, SIGKILL | clean on all but SIGKILL; SIGKILL left `cursor_flag=0` and an open row |
| S-21 | a `Console` subclass whose `show_cursor` returns `False`, status, SIGKILL | `cursor_flag=1`; only the open row remains |
| S-22 | `transport.run_ssh_with_reconnect` from `src/` at `371e86d`, with `["sh", "-c", "printf '\033[?1003h\033[?1006h'; sleep 30"]` as ssh and a `touch` as the cleanup command; SIGINT, SIGTERM, SIGHUP | SIGINT: modes cleared, cleanup ran, outcome written. SIGTERM and SIGHUP: `mouse_any_flag=1`, `mouse_sgr_flag=1`, no cleanup, no outcome |
| S-23 | as S-22 with a stand-in that runs `stty raw -echo -opost` and traps TERM to `stty sane`; handlers: none, `sys.exit`, forward-to-child | `sys.exit`: modes cleared, cleanup ran, termios left `-icanon -echo -opost`. Forward: all restored. Both outcomes `error` |

The prototype owner from S-20, reduced to its shape:

```python
@contextlib.contextmanager
def live_region(renderable):
    if not _interactive():  # TTY, TERM not dumb (the real owner adds foreground and OPOST checks)
        yield None
        return
    console = Console(stderr=True, force_terminal=True, force_interactive=True, markup=False, highlight=False)
    live = Live(renderable, console=console, transient=True, redirect_stdout=False, redirect_stderr=False)
    with _lock:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            _saved[signum] = signal.signal(signum, _on_signal)  # _on_signal: teardown(), then SystemExit
        _active = live
        live.start(refresh=True)
    try:
        yield live
    finally:
        teardown()  # idempotent: stop the live, restore the saved handlers


def record(text):  # while active, print above the live line
    with _lock:
        (_active.console.print(text, soft_wrap=True) if _active else sys.stderr.write(text + "\n"))


def exec_handoff(argv):
    teardown()
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(argv[0], argv)
```

The scratch directory holding these scripts was deleted after the doc was written; it had no other
consumer.

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none yet; auto-populated by verify_and_remediate node)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** R-51
**Model:** `claude-opus-5-5`
**Date:** 2026-10-13

The prompt is the task brief as received. The maintainer's name is replaced with "the maintainer",
and local filesystem paths are replaced with the file they name.

````text
Objective: /research-doc AI-CLI-g013 in ai-cli-utils (PUBLIC repo). The maintainer's verbatim ask,
2026-10-13: "i want live displays. why would that be an issue for claude code? can you /delegate
opus /research-doc and/or /delegate opus /bug-fix those issues and bugs to figure out how we can
enable live displays for our tmux process-wrapped cc sessions? i want that." The maintainer also
decided D-1 of docs/designs/launcher-output-standard.md: structlog for the record model with Rich
rendering, and live displays wanted, which lifts the design's live-display ban.

Question: how can the `ai` launcher use Rich live displays (Live, Progress, Status/spinners, an
updating reload or status region) safely, in both the local tmux-wrapped path and the remote `-R`
path over ssh? Investigate against the real code: the exec handoff to tmux/claude (main.py
`_exec_handoff` once AI-CLI-8h3u lands, else `os.execvp` sites); the generated session-script
supervisor (`src/ai_cli/session_script.py`) and its tcsetpgrp/foreground-group and SIGTTOU
constraints; teardown on every exit path, including crash, signal, exec and SIGKILL (the
AI-CLI-w679 terminal-left-broken bug, `git log --grep w679`); Rich's own behaviour (Live's refresh
thread, alternate screen, cursor hiding, `transient=True`, `redirect_stdout`); non-TTY and NO_COLOR
degrade; interleaving with remote output. Measure, do not assume: build small throwaway spikes in
your worktree (scratch dir, not committed) that exec while a Live is active, crash mid-Live, and run
under tmux, and record what the terminal state is afterwards (for example `stty -a`, cursor
visibility). Cite Rich docs and source and any prior art.

Deliverable: `docs/research/rich-live-displays-in-wrapped-sessions.md`, from the canonical research
TEMPLATE (follow its required sections, citations mandate and `## Appendix: Research Prompt` with
this prompt, model and date). It must cover: why each hazard happens (mechanism), which are real on
this codebase (with spike evidence), the safe pattern for each display kind and path, a concrete
recommendation for the design (which displays where; what replaces the ban: for example a guard that
every Live is entered through one context manager that is torn down before any exec and on every
signal), and a list of bugs found. File each bug you find as a short note in the doc's findings
list; do not fix them. Add the research doc to any docs index the repo keeps.

Gates: markdown lint and link checks via pre-commit on commit (never --no-verify). Public-repo
rules: no personal names, private hosts, session names, or tracker ids other than AI-CLI-*.
````

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

(none yet; auto-populated at commit from the validated provenance ledger)

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-10-13: first run, `claude-opus-5-5`, effort high. 23 spikes on macOS with Rich 15.0.0 and
  tmux 3.7c against `371e86d`, plus a read of open PR #301 at `7477867`.

<!-- /doc:region name="run_history" -->
