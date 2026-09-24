---
title: "The supervisor promotes the pid it backgrounded, so a spawning wrapper leaves the terminal owned by an empty process group"
category: bug
tags: [bug, session-launch, process-groups, terminal, tmux, uv, linux, proc]
status: fixed
template_version: "bug-1.0.0"
---

# The supervisor promotes a process group with no members

**Status:** fixed -- reproduced on a live wedged pane, root-caused from `/proc`, regression suite frozen red before the fix, hard gate green

**Task:** AI-CLI-dw1g

**Created:** 2026-09-24

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Rejected Hypotheses](#rejected-hypotheses)
- [Scope of Fix](#scope-of-fix)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)
- [Fix Log](#fix-log)
- [Appendix: Evidence](#appendix-evidence)

## Summary

`ai c <name>` created its tmux session, the pane stayed alive, and the agent never started. The pane
printed nothing for thirty seconds, then one line:

```
ai-cli: could not promote child process group to terminal foreground
```

After that the pane could not be interrupted. Fifteen `^C` presses were recorded in the scrollback
and none of them did anything.

All three of those symptoms come from one fact. The supervisor promoted `$!` -- the pid it
backgrounded -- as a process group id. `$!` is the pid that calls `setpgrp()` only when every wrapper
between the shell and the exec shim `exec`s through. On this host `python3` is a shell shim that runs
`uv run`, and **`uv run` spawns the interpreter as a child and waits rather than exec'ing**. So the
shim, and therefore `setpgrp()`, ran one level further down, and the new group's id was the
grandchild's pid. The group named by `$!` had no members at all.

## Reproduction

Observed directly on a live wedged pane rather than reconstructed, which is what made it tractable.
`tmux list-panes -a` was the first discriminator: every healthy session reported
`pane_current_command = zsh`, the stuck one reported `uv`.

The process tree, read from `/proc` (`comm`, `PPid`, `NSpgid`, and field 8 of `stat` for `tpgid`):

```
3789512 zsh     (supervisor)        pgid=3789512   tpgid=3789562
├─ 3789560 uv   (ticker wrapper)    pgid=3789512
│   └─ 3789568 zsh (the ticker)     pgid=3789568
├─ 3789562 uv   ($_child_pid)       pgid=3789512
│   └─ 3789569 python  state=T      pgid=3789569
├─ 3789564 main2                    pgid=3789512
└─ 3789566 main2                    pgid=3789512
```

Then the decisive measurement -- a scan of every `/proc/*/status` for `NSpgid: 3789562`, the group the
terminal had been handed:

```
NONE -- the terminal foreground group is empty
```

The readiness file was present and held the five bytes `ready`, so the shim had run and reported.
`python` was in state `T`: it had stopped itself for the handoff and was never continued.

## Root Cause

`_supervisor_promote_child` in `src/ai_cli/session_script.py` waited for the child's readiness file
and then ran, with `$_child_pid` as the argument:

```python
os.tcsetpgrp(0, pgid)
os.killpg(pgid, signal.SIGCONT)
```

The child shim wrote the fixed word `ready`, which carries no identity, so `$_child_pid` was the only
value the supervisor had. With a spawning wrapper in between, that value names a group with no
members, and every symptom follows:

1. **The pane printed nothing.** `killpg` went to the empty group, so the stopped grandchild never
   received its SIGCONT and never exec'd the session shell.
2. **The 30-second failure.** The promotion never reported success, so the loop ran its full
   3000 iterations at 10 ms and then failed.
3. **Ctrl+C did nothing.** `tcsetpgrp` had already handed the terminal to the empty group, and SIGINT
   is delivered to the terminal's foreground group. There was no process in it to receive the signal,
   and the supervisor's own INT trap only increments a counter.

The same root cause also silently defeated the earlier fix for `AI-CLI-jpnd`. That fix continues the
stopped child before waiting on it, via `kill -CONT -"$_child_pid"` -- which names the same empty
group, so the wait still blocked forever on a child that could never die.

## Rejected Hypotheses

- **A `uv run` stall in the critical path.** The leading hypothesis from the earlier window, and
  measured false: the shimmed `python3 -c pass` runs in 0.02 s against 0.01 s for
  `/usr/bin/python3`, so the 30-second window was never a latency problem. `uv` being in the path
  still mattered, but for its process *shape*, not its speed.
- **`os.isatty(0)` false in the child, so the shim skipped `setpgrp()` entirely.** Falsified by the
  readiness file existing with content, and by `python` sitting in state `T`, which only the shim's
  own `SIGSTOP` explains.
- **A buffered readiness write lost to the `SIGSTOP`.** Plausible from reading the code, and
  falsified by the file holding five bytes on disk. Hardened anyway, because it depends on CPython
  refcount timing for something the handoff cannot retry.
- **`tcsetpgrp` refusing an empty group.** Assumed while reading, then contradicted by the
  measurement: `tpgid` was 3789562 while that group had no members, so the call had in fact
  succeeded. The failing call was `killpg`.
- Earlier falsified hypotheses for the same launch failure, recorded on the issue: lease contention,
  uv cache-lock contention, a network stall, `direnv`, and shell init.

## Scope of Fix

In scope: stop inferring the child's process group. The mechanism should report it. This is a
contained change to one template and its handoff, well below the redesign threshold.

Out of scope, surfaced rather than fixed: `~/.local/bin/python3` is a hand-written shim whose own
comment says it exists because the OS `python3` on a particular Windows host is a Microsoft Store
stub, and it routes every `python3` call on this Linux host through `uv run`. Removing it would also
remove the spawn layer, but it is host configuration with a blast radius far beyond this launcher,
and a correct handoff must not depend on what `python3` resolves to in the first place.

## Fix

Two coordinated halves, both in `src/ai_cli/session_script.py`:

1. **The child reports its group.** `CHILD_BODY_SHIM` now writes `str(os.getpgrp())` instead of
   `ready`, using `os.write` on a raw descriptor so the value is on disk before the next statement
   stops the process.
2. **The supervisor promotes the reported group.** `_supervisor_promote_child` reads the value,
   validates it is numeric, and passes it to `tcsetpgrp`/`killpg`. It never uses `$_child_pid` for
   this again. The failure path now also continues and terminates the reported group, restores the
   terminal to the supervisor before exiting, and returns a distinct status for an interrupt so a
   Ctrl+C during the handoff ends the wait instead of being counted and ignored for 30 seconds.

The failure messages now distinguish "never reported a group" from "reported group could not be
promoted" from "interrupted", because those have different causes and the single old message covered
all three.

Both shims were extracted to module constants (`CHILD_BODY_SHIM`, `PROMOTE_CHILD_SNIPPET`) so the
handoff can be executed in tests instead of asserted about as template text.

## Verification

- `tests/test_supervisor_terminal_handoff.py`, 6 tests, run on a real pty with a real spawn layer.
  Frozen **red before the fix**: 5 failed, each because the child wrote `'ready'` rather than a pgid.
- **Ablation after the fix:** reverting only the shim's write returns the suite to 5 failed / 1
  passed; restoring it returns 6 passed. The one test that passes in the ablated state is the one
  that pins the old broken behaviour, which is the control.
- The generated script parses under both interpreters: `zsh -n` and `bash -n` both exit 0.
- Exit-code propagation through the surviving `uv` layer measured directly: 77, 78 and 79 all
  round-trip, so the session loop's restart and clean-exit semantics are unaffected.
- `ruff check` and `ruff format --check` clean.

## Lessons Learned

**`$!` is the pid you backgrounded, not necessarily the pid that did the work.** Any wrapper that
spawns instead of execs breaks that identity, and nothing in the shell tells you which one you got.
Where a value identifies something a later step must act on, have the step that creates it report it.

**A terminal handed to a group with no members is worse than a failed handoff**, because it removes
the operator's ability to interrupt. Restoring the foreground group before erroring out costs one
line and is the difference between a pane that reports a failure and a pane that cannot be closed.

**A fix that names the wrong group fails silently and looks like a different bug.** `AI-CLI-jpnd`'s
SIGCONT was correct in intent and inert in practice for a year of edge cases, because it named
`-$_child_pid`.

**The positive control did the diagnostic work again.** `pane_current_command` of `uv` versus `zsh`
across four panes located the defect before any code was read.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-09-24 | Captured the live wedged pane's tree, `tpgid` and group membership from `/proc` | Foreground group proven empty; `python` proven stopped |
| 2026-09-24 | Froze `tests/test_supervisor_terminal_handoff.py` against unfixed code | 5 failed, 1 passed, all for the reported reason |
| 2026-09-24 | Child reports its pgid; supervisor promotes the reported group | 6 passed |
| 2026-09-24 | Ablated the shim write to confirm the suite still detects the defect | 5 failed, restored to 6 passed |
| 2026-09-24 | Existing suite caught a regression in the new interrupt check: `_supervisor_int_count` is cumulative for the supervisor's life, so one earlier Ctrl+C aborted every later promotion and the agent stopped relaunching | Fixed by comparing against a per-attempt baseline; `test_stale_session_reaper.py` 70 passed |
| 2026-09-24 | Updated the promotion-failure assertion in `test_stale_session_reaper.py` for the more specific message, and redacted an employer name from this doc for the public-package hygiene gate | Full suite 3032 passed, 4 failed, all four pre-existing and filed separately |

## Appendix: Evidence

Pane scrollback at the point of failure, 137 bytes in total:

```
ai-cli: could not promote child process group to terminal foreground
^C^C^C^C^C^C^C^C^C^C^C^C^C^C^C
```

The host's `python3`, which supplies the spawn layer:

```
#!/usr/bin/env bash
# Real CPython shim - the OS `python3` on the Windows host is a Store stub. (comment paraphrased)
exec uv run --no-project --python 3.14 python "$@"
```

The `exec` line is the load-bearing part: it replaces the shim with `uv run`, and `uv run` is what
then spawns rather than execs.
