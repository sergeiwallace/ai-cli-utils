---
title: "A remote session can start preflight after a recorded double interrupt"
category: bugs
tags: [session, tmux, signals, direnv, remote]
status: fix-verified-remotely
severity: P1
template_version: "bug-1.0.0"
---

# A remote session can start preflight after a recorded double interrupt

**Status:** fix-verified-remotely

**Created:** 2026-09-02

## Symptoms

Remote tmux sessions launched with `ai c <index> -R` could remain on repeated
environment-loading output after the documented double-Ctrl+C exit gesture. In
one report, the session exit marker already existed while replacement child
processes continued to start. A separate preserved report described a tmux dead
pane instead of a clean termination.

## Environment and reproduction

The reported environment was a remote Linux tmux session reached over a remote
terminal transport. The preserved dead-pane session had already been removed
before this investigation resumed, but its capture showed several Ctrl+C
presses during `direnv` loading and tmux's `Pane is dead (status 0, ...)`
placeholder.

The deterministic local reproduction uses the real generated session script,
a real subprocess, and the selected bash and zsh shells. It creates the same
persisted exit marker that the second Ctrl+C records, then observes whether a
replacement child invokes a real `direnv` executable before terminating.

Before the fix, both shells invoked `direnv`; the regression assertion failed:

```text
AssertionError: an already-requested exit must not start direnv
```

### Dead-pane follow-up reproduction

The capture's timestamp preceded the clean-supervisor-exit teardown fix, which
explicitly calls `tmux kill-session -t "$tmux_session"` after a clean child
exit. It followed the child SIGINT fix, so the preserved output alone cannot
establish which script revision was running when the pane died. Because the
launcher intentionally enables the window-local `remain-on-exit` option, a
process which exits before teardown leaves the pane visible and dead; a
supervisor that reaches teardown removes the whole session.

On the remote Linux host, the generated-wrapper Ctrl+C regression passed under
both bash and zsh. A focused clean-exit regression passed locally and asserts
that the supervisor invokes `tmux kill-session`. The repository's real-tmux
version of that test could not complete: its test-only `tmux` PATH wrapper
recursively execs itself, which is separately tracked as an unrelated
clean-exit test failure. The disposable remote test process group and its
temporary state were removed after that observation.

## Root cause analysis

```text
second Ctrl+C records the session exit marker
  → a replacement child starts
  → its per-launch loop starts watchers and preflight work before consulting the marker
  → `run_agent` can begin `direnv export bash`
  → an exit-requested session visibly loads or stalls instead of terminating
```

The child did eventually check the marker inside `run_agent`, but only after
the `direnv` initialization block. That ordering violates the exit-marker
contract: a persisted final-exit request must take precedence over all
replacement-child startup work.

The dead-pane artifact has a different, already-fixed causal chain:

```text
wrapper or supervisor exits before its deliberate final teardown
  -> tmux `remain-on-exit` retains the pane after its process exits
  -> the attached remote client sees tmux's dead-pane placeholder
```

Commit `72721a7` closes that chain for clean supervisor exits by killing the
tmux session before the supervisor exits. The captured artifact predates that
commit, and the current generated-wrapper signal regression confirms that a
preflight Ctrl+C is trapped rather than killing the wrapper by its default
SIGINT disposition.

### Hypothesis ledger

| Hypothesis | Check | Result |
|---|---|---|
| The child ignores the persisted exit marker before preflight | Generated-script subprocess regression with a real `direnv` executable | Confirmed |
| `remain-on-exit` is retaining a deliberately cleaned session | Source inspection and a focused clean-exit regression show the supervisor kills the tmux session before its own exit | Rejected; `remain-on-exit` only exposes an exit path that bypasses cleanup |
| Repeated Ctrl+C still kills the current child wrapper by its default SIGINT disposition | Generated-wrapper signal regression on the remote Linux host under bash and zsh | Rejected; both shell variants pass |
| The original dead pane was produced before its clean-teardown fix existed | Compare the pane-capture timestamp with commit `72721a7` | Confirmed |
| The issue is specific to the remote transport | The deterministic ordering defect reproduces under local bash and zsh subprocesses | Rejected for this causal path; remote transport may still affect other symptoms |

### Scope-of-fix decision

Scope signals: three or more unrelated subsystems — no; new shared abstraction
— no; public contract change — no; repository boundary — no; broader pattern
— no. The change is one guard in the session child loop, reversible, and scoped
to the existing exit-marker protocol. A contained fix is appropriate.

## Fix

The generated child checks its persisted exit marker at the beginning of every
per-launch loop iteration. If present, it exits with the existing clean-final
status before starting watchers, resolving a continue target, or initializing
direnv. The persistent supervisor already recognizes that status and kills its
tmux session.

No additional production change was made for the dead-pane report. Its required
clean-exit teardown was already present in commit `72721a7`; the historical
artifact was created before that commit and does not reproduce with the current
generated-wrapper signal test.

## Verification

1. Frozen regression RED before the production edit: two failures, one for
   bash and one for zsh, both showing that `direnv` was started.
2. Frozen regression GREEN after the guard: two passes.
3. Disabled the guard without changing the frozen test: the same two expected
   failures returned; restored it and confirmed two passes again.
4. Nearby Ctrl+C and clean-session-exit subprocess tests passed: 6 passed,
   1 skipped.
5. Current focused local verification: `UV_CACHE_DIR=/tmp/ai-cli-utils-uv-cache
   uv run pytest -q -n0` for the clean-exit assertion and preflight Ctrl+C
   regression: 4 passed.
6. Current remote verification: the preflight Ctrl+C generated-wrapper
   regression passed under both bash and zsh on a Linux host with tmux 3.7c.
7. The remote session named in the report was confirmed absent; no unrelated
   active sessions were changed.

The real-tmux clean-exit test remains blocked by its separately tracked
test-wrapper recursion, not by tmux socket access or remote network
connectivity. It was not changed in this bug fix.

## Lessons learned

A durable exit marker is an inter-process command, not a hint for the agent
launch path. Replacement-child loops must honor it before any work that can
block or emit misleading startup output. The regression asserts both the
positive outcome (clean supervisor termination) and the negative constraint
(no `direnv` invocation).

## Fix log

### 2026-10-18 — acquisition before terminal cleanup

A separate acquisition failure was reproduced locally in real tmux using the
frozen pre-feature supervisor and the acquisition body rendered from template
commit `dedd4ffc`. In inherited extended keyboard mode, `send-keys C-c` produced
literal `CSI 27;5;99~` input, rather than SIGINT. Signal disposition and retry
sleep latency did not explain this reproduction: the terminal encoded the key
before a signal could be generated. This establishes one mechanism matching the
reported symptom, without proving which terminal mode was active on another host.

The terminal cleanup in the generated child ran after generation acquisition.
The installed acquisition helper now resets keyboard modes through its terminal
stderr (falling back to the controlling terminal), restores signal input, and
records the existing supervisor exit marker on SIGINT. The old supervisor then
stops instead of respawning a child whose ordinary interrupt status it treats as
retryable. Its captured generation reply remains free of terminal control bytes.

Bulk enrolment previously wrote only registry bookkeeping. It now atomically
regenerates validated stable scripts too, including already registered sessions,
and serializes competing enrolments' writes. Existing heartbeats also perform
recovery after a package reinstall, covering agents that never restart. Ownership,
generations and existing policies remain unchanged; new recovery rows use `never`.

The four real-tmux upgrade and interrupt assertions failed against the unchanged
production implementation, then passed after restoring the fix. The fixture keeps
the pane leader stable and checks both successful registration/child startup and
clean interrupt termination without agent startup. No live sessions were used.

An old child permanently parked after twenty failures invokes only shell `sleep`.
It cannot execute upgraded Python or observe a regenerated script while parked.
Enrolment can repair its registry and disk script, but waking that child requires
operator intervention, outside a recovery that promises not to restart sessions.
Regeneration prevents the same permanent park on its next child start. Operator
upgrade steps and this limitation are documented in README.

| Date | Change | Notes |
|---|---|---|
| 2026-09-02 | Regression and loop guard | Implemented as `implement` / high effort because process-group signal handling has multiple plausible failure modes. |
| 2026-09-02 | Dead-pane follow-up | Used a live remote shell and tmux evidence. No production edit: the artifact predates existing clean-teardown fix `72721a7`; current bash/zsh signal regression passed remotely. |

### 2026-11-15: return to the calling shell after an interrupt

The current generated supervisor reproduced a different wrong destination on
macOS with real tmux. After an intentional interrupt it called
`_supervisor_preserve_pane`, skipped its fenced tmux cleanup, and executed
`exec "$SHELL" -i` in the worktree. That shell was inside the session, so an
attached client stayed connected instead of returning to the calling shell.
The existing interrupt tests explicitly required that destination and therefore
did not detect the defect.

The frozen regression in `tests/test_session_interrupt_shell.py` starts an outer
interactive shell in a separate directory. A fake remote executable attaches to
the real session through the real SSH reconnect runner, without network access.
The local variant exercises the launcher's real exec handoff. Both record the
outer shell's PID and directory before and after the launcher. On base
`87296d5738b1361ece7f5318e490e264a61f0003`, this command failed because the
post-launch record never appeared; the captured pane contained
`Auto-restart stopped. Returning to an interactive shell.`:

```bash
uv run pytest -q -n0 tests/test_session_interrupt_shell.py -k 'attached_launcher and bash and c-True' --maxfail=1
```

The fix removes pane preservation from promotion interruption, recorded final
interrupt, and clean child-exit paths. Existing generation fencing removes only
the owned tmux session; the attached client then exits to its parent shell.
Policy-driven worktree shells and failed-remote-launch recovery remain available.
The final-exit message now names the calling shell. Output and ownership tests
follow that destination while retaining their original assertions about logging,
single launches, and replacement-session survival.

The hypothesis that the current remote launcher replaces the original local
shell was rejected in the subprocess reproduction: the same PID and directory
survive attachment and exit. This does not establish why a particular terminal
application closed a live pane in the original report. A pane configured to run
the launcher as its initial process has no pre-existing shell to return to.

The historical registry disappearance cannot be reconstructed from the report.
Current acquisition already reconstructs missing registration from validated
launch metadata; legacy acquisition stops after twenty failures or an interrupt.
Those existing paths pass the focused acquisition/lifecycle suite on the base.
The new suite also removes its own registration during a running session to
exercise final-exit persistence refusal without agent relaunch.

Scope decision: one reversible change to the existing supervisor's termination
paths, no registry changes or new ownership boundary. All broader-design scope
signals are absent. The tests intentionally replace the old interrupt-to-worktree
shell contract with return-to-caller, as requested. No live session is touched.
Live remote SSH/mosh and terminal-application confirmation remain with the caller.

A second frozen regression pins the agent's elapsed time below three seconds
and sends raw Ctrl+C to a successful fake agent. It reproduced another wrong
destination after the first fix: the quick-start guard classified status zero
as failure and opened the remote recovery shell. Three presses left that shell
at its prompt. The corrected child treats status zero and 130 as intentional
exits regardless of elapsed time. The prior short-success-as-failure behavior
is intentionally dropped: a raw terminal interface can consume Ctrl+C and
return zero without delivering SIGINT to the wrapper. Failed starts still use
the existing quick/consecutive-failure guards and remote recovery shell.

The early-interrupt regression command was:

```bash
uv run pytest -q -n0 tests/test_session_interrupt_shell.py -k 'early_raw_interrupt and bash and cx' --maxfail=1
```

It produced `1 failed, 71 deselected in 41.62s` with the prior three-second
condition and `1 passed, 71 deselected in 4.93s` with the corrected condition.

The regression fixture also waits for complete command output rather than file
creation: a shell redirect creates an empty file before `printf` writes it.
An initial wider run exposed that assertion race; the corrected fixture was
checked against the base script again and retained the expected failure.
Captured pane output is drained completely and checked for tracebacks and
`KeyboardInterrupt`. Missing-registration interrupts are separated by the
observable persistence-failure message, so queued POSIX signals cannot coalesce
before the persistence helper starts.

The final wider supervisor run produced `1 failed, 808 passed, 2 warnings in
500.62s (0:08:20)`. The failure was in the new fixture's post-return proof:
the original PID and directory matched, but sending Ctrl+C and the proof command
together let the terminal discard queued input. The fixture now waits for a new
prompt after Ctrl+C before sending the command. The exact failing parameter
passed in isolation (`1 passed in 6.58s`), and the complete interrupt matrix was
rerun with this correction. The warnings observed a concurrent terminal-profile
writer while the test's isolated home directory remained intact.

The wider command was:

```bash
uv run pytest -q -n2 tests/test_session_interrupt_shell.py tests/test_session_begin_retry.py tests/test_session_begin_recovery.py tests/test_session_lifecycle.py tests/test_session_lifecycle_script.py tests/test_session_recovery_shell.py tests/test_reload_progress.py tests/test_session_script_sinks.py tests/test_session_terminal_reset.py tests/test_session_script_bash32.py tests/test_restart_run_state.py tests/test_session_restart.py tests/test_remote_connection_durability.py tests/test_stale_session_reaper.py --maxfail=2
```

Loading the original production script from `87296d5738b1361ece7f5318e490e264a61f0003`
into memory and running the corrected fixture's attached-launcher regression
again produced `1 failed, 71 deselected in 52.56s`. The failure still showed
the interactive worktree shell and the absent post-launch record, before the
fixture's prompt synchronization runs. The corrected fixture preserves RED.

Final interrupt-matrix verification:

```bash
uv run pytest -q -n2 tests/test_session_interrupt_shell.py --maxfail=1
```

Result: `72 passed, 2 warnings in 223.40s (0:03:43)`. Both warnings observed
concurrent changes to the real worktree registry while test-state redirection
remained intact. Formatting and lint checks passed across `src/`, `tests/`, and
`scripts/`; source type checking reported zero errors, warnings, or information.
All changed documents passed the repository's document validation.

| Acceptance criterion | Behavioral coverage |
|---|---|
| Interrupt stops the loop within three presses | Signal, raw, early raw-clean, ignored-first-interrupt, and repeated-teardown cases |
| Original directory is retained | Before/after directory equality and a separate worktree directory |
| Original shell is retained | Before/after PID equality and a command executed after another Ctrl+C |
| Remote shell exit leaves the local pane open | Failed-launch recovery followed by `exit`, with caller PID/directory checks |
| Missing registration still permits exit | Real registry row removal, interruption during persistence refusal, no relaunch or traceback; existing legacy-acquisition tests |
| Claude Code and Codex, local and remote | `c`/`cx` and local/fake-SSH parameter combinations, under bash and zsh |

Already running supervisors keep their loaded program; relaunch is required to
receive the new supervisor exit behavior. The package update does not modify
another session's running process or registry ownership.
