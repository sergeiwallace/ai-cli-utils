---
title: "A liveness check guarded the status collection, so a fast child exit became an endless respawn"
category: bugs
tags: [session, supervisor, tmux, signals, process-group, teardown, race]
status: fix-deployed
severity: P1
related_docs:
  - docs/bugs/ai-cli-2139-session-exit-leaves-stopped-process.md
  - docs/bugs/ai-cli-9qdr-session-exit-reaps-session-worktree.md
  - docs/bugs/supervisor-direct-signal-contract.md
---

# A liveness check guarded the status collection, so a fast child exit became an endless respawn

`_supervisor_wait_for_child` collected the child's exit status inside a loop whose condition was
`kill -0 "$_child_pid"`. When the child exited quickly enough that the shell had already reaped it
before the function was entered, that condition was false on the first evaluation, the loop body
never ran, `wait` was never called, and the function returned the `0` its status variable had been
initialised to.

`0` is neither `77` (clean local final exit) nor `79` (remote recovery-shell completion), and no
interrupt-exit marker was present, so the supervisor's loop read it as "the child needs replacing"
and started another one. That child exited just as fast, and so did the next. The supervisor spun,
never reached its teardown block, and never killed the tmux session -- producing exactly the stale
session the reaper machinery exists to prevent.

## Symptoms

- `test_given_renamed_supervisor_during_ownership_bootstrap_when_clean_exit_then_replacement_survives`
  failed at `_wait_for_missing_session` with `tmux session did not terminate`, reliably: 6 of 6
  runs on the affected machine.
- The failing run took ~12 s, the whole of it spent respawning. Trace instrumentation recorded
  **136 child launches** in a single run, roughly one every 70 ms.
- Neither branch message that wraps the teardown kill was ever emitted -- not the fence mismatch
  warning and not the ownership-not-established warning. An earlier investigation read that silence
  as "the supervisor is blocked upstream of ownership". It was not blocked; it was looping.
- The pane was empty, because nothing in the respawn path prints anything.

## Causal mechanism

The child is launched into its own process group by a short python `exec` wrapper. When stdin is a
tty the wrapper calls `setpgrp()`, writes a readiness file, and `SIGSTOP`s itself so the supervisor
can put its group in the terminal foreground before it runs:

    _child_pid=$!
    if ! _supervisor_promote_child; then ...
    _supervisor_wait_for_child

`_supervisor_promote_child` polls for that readiness file and then spawns a second python process to
`tcsetpgrp` and `SIGCONT` the group. On the measured machine that promotion takes about 45 ms, and
it returns roughly 1.4 ms after delivering the `SIGCONT`.

That is ample time for a child which exits immediately to terminate *and* be reaped by the shell's
own `SIGCHLD` handling before `_supervisor_wait_for_child` is entered. At that point:

- `kill -0 "$_child_pid"` fails, because the OS no longer has the pid;
- but `wait "$_child_pid"` would still have returned `77`, because the shell retains an exited
  child's status until it is waited for exactly once.

Those are two different questions, and the loop asked the wrong one. Measured directly with the
function's own body in real bash:

| state when the wait begins | `_supervisor_wait_for_child` | plain `wait` |
|---|---|---|
| child still running | 77 | 77 |
| child exited and already reaped | **0** | **77** |

The third column is the important one. The status was always available; the guard is what threw it
away.

### Why the tty matters, and why a sibling test passed over it

`test_given_clean_child_exit_when_supervisor_finishes_then_tmux_session_is_removed` uses a child
body that also exits `77` immediately, and it passed throughout. It launches the supervisor with
`</dev/null`, so `[[ -t 0 ]]` is false: the wrapper skips `setpgrp`/`SIGSTOP` entirely and
`_supervisor_promote_child` returns at once. With no promotion there is no gap between the fork and
the wait, the child is still unreaped, and the status survives.

So the passing test and the failing one differ in exactly the thing that opens the window. Every
real `ai c` session runs in a pane **with** a tty, which is the configuration the defect affects.

### Why the trigger was reached at all

In the failing test the barrier releases, ownership is established within ~66 ms, the test observes
the generation marker and touches its `finish-child` file -- and only then does the supervisor
launch its first child. Measured: `finish-child` created at `...430.635`, first child launched at
`...430.844`. Every child therefore saw the file already present and exited `77` at once. The test
did not intend to exercise a fast exit; it did so as a side effect of ownership being quick.

## Rejected hypotheses

Four were falsified in an earlier window and are recorded so they are not re-run:

1. **A missing controlling terminal.** Identical failure under a real pty via `/usr/bin/script`.
2. **`python3` resolution or PATH.** The harness passes the full inherited PATH.
3. **The ownership fence.** The fence construct was reproduced standalone on tmux 3.7c: the format
   evaluates to 1, `if-shell` kills the original by id, and the replacement survives.
4. **The signal-model gate.** Inside a detached pane `pgid == tpgid` with a real tty, and the gate
   verifies.

Two more were falsified while fixing it:

5. **A test-fixture variable reuse.** The helper rebinds its `ready` variable to the barrier path
   and exports that as the child-ready environment variable, so with a barrier the two signals share
   one file. Real, and worth cleaning up, but not causal here: the child-ready path the supervisor
   actually uses is an independent `mktemp`, and this test's child body never invokes the fake agent
   that reads the environment variable.
6. **A blocked or stopped child.** The trace showed the child gone on every iteration
   (`alive=no`, 135+ times per run) rather than alive-and-stopped.

### The probe that hid the bug

Adding a single `write()` to the child's exec wrapper to log its argv made the test **pass**. The
extra syscall delayed the child past the window. Any instrumentation on the child side destroys this
defect; only supervisor-side probes can observe it.

## The RED test

`test_given_child_exits_before_the_supervisor_waits_then_the_session_still_terminates` -- the
tty-backed twin of the sibling test above. It drives the real generated supervisor through real tmux
with a child body that exits `77` immediately, then asserts both halves:

- `_wait_for_missing_session` -- the session must actually be torn down (the symptom);
- the child launched exactly **once** (the mechanism). With the defect present this runs into the
  hundreds, so the assertion distinguishes "torn down for the right reason" from "torn down".

Confirmed RED on unfixed code with `Failed: tmux session did not terminate`.

## The patch

Run the wait first and use liveness only to decide whether to wait *again*:

    -        while kill -0 "$_child_pid" 2>/dev/null; do
    -          wait "$_child_pid"
    -          _child_wait_status=$?
    -        done
    +        while :; do
    +          wait "$_child_pid"
    +          _child_wait_status=$?
    +          kill -0 "$_child_pid" 2>/dev/null || break
    +        done

The loop still exists for its original reason -- `wait` is interruptible by a trapped signal in bash
and zsh, so a record-only supervisor signal must not be mistaken for child termination. That case is
unchanged: an interrupted `wait` returns >128 while the child is still alive, `kill -0` succeeds, and
the loop waits again. What changes is that the status is now collected before liveness is consulted,
so it cannot be discarded by a child that finished early.

## GREEN results

- The frozen regression test passes; the originally-failing reaper test passes.
- Both pass **10 of 10** consecutive runs. Runtime dropped from ~12 s to ~2.1 s, because the 12 s
  was the respawn loop.
- Mutation-proved against committed state: restoring the old loop turns **both** tests red;
  restoring the fix turns both green.
- The full `tests/test_stale_session_reaper.py` file passes: **63 passed**.
- Repository suite: **2905 passed, 1 failed, 5 skipped**. The one failure is
  `TestSelfUpdatePreservesEditableInstall::test_given_editable_venv_when_plain_install_runs_then_marker_is_destroyed`,
  which is unrelated and pre-existing -- it fails the same way in the main tree at this branch's base
  commit with none of these changes present, and it **passes in isolation** on that same commit, so
  it is a parallel-run isolation flake rather than a product defect. Tracked separately; not fixed
  here, because a test-isolation investigation is out of proportion to this fix and merging them
  would make both harder to review.

## Prevention lesson

**A liveness check is not a status-availability check, and a guard must ask the question the
decision actually turns on.** `kill -0` answers "does the OS still have this pid". The decision being
made was "can a status still be collected", which in a shell depends on whether *the shell* has
waited yet -- a different lifetime, deliberately longer. Between those two lifetimes sits a window,
and a fast child lands in it every time.

Two corollaries worth keeping:

- **An initialised default is a silent wrong answer.** `_child_wait_status=0` turned a skipped
  measurement into a confident `0` that happened to mean "restart". A sentinel the caller must
  handle would have surfaced this on the first fast exit instead of years later.
- **Silence is not evidence of a location.** The absence of both teardown branch messages was read
  as "execution never got here, so it is blocked upstream". Both halves were wrong in a way that
  cost an investigation: it did never get there, but because it was spinning. An entry marker turned
  a three-hypothesis guess into a one-line answer.
