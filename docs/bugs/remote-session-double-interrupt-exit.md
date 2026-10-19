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
