---
title: "Repeated direct supervisor interrupts can terminate a live session"
category: bugs
tags: [session, tmux, signals, supervisor]
status: resolved
severity: P1
template_version: "bug-1.0.0"
---

# Repeated direct supervisor interrupts can terminate a live session

**Status:** resolved

**Created:** 2026-09-02

## Symptoms

Two `SIGINT` signals sent directly to a generated session supervisor within a
short interval could terminate its live child and finish the session. This
contradicted the signal contract: direct supervisor `SIGINT` and `SIGWINCH`
must be recorded without relaying either signal to the foreground child.

## Environment and reproduction

The focused generated-supervisor regression starts a child that exits once,
then starts a second child that records `TERM`. It sends two `SIGINT` signals
to the supervisor PID and asserts that both the supervisor and second child
remain live and that the child received no signal.

Before the fix, the regression failed for both installed supported shells:
one variant exited the supervisor and the other left the supervisor live after
the child had been terminated. In both cases the child-survival assertion
failed, which is the required contract violation.

The earlier child-restart double-Ctrl+C test also failed for both shells in
this checkout, but its helper supplied stdin through a pipe and sent `SIGINT`
to the supervisor's whole process group. That is not terminal delivery: a
real terminal directs input to the foreground child process group, not the
background supervisor group.

## Root cause analysis

```text
second direct SIGINT to the supervisor
  -> _supervisor_record_int calls _supervisor_term
  -> _supervisor_term sends TERM to the live child
  -> the child exits and the session is terminated or left in an interrupted wait
```

The double-Ctrl+C escape was added to the supervisor even though the child
already owns the terminal-generated gesture. Signals addressed to the
supervisor carry no origin metadata, so a direct signal cannot safely be
treated as terminal input.

### Hypothesis ledger

| Hypothesis | Check | Result |
|---|---|---|
| The direct supervisor trap relays the second interrupt | Read the trap and run the frozen subprocess regression on Bash and zsh | Confirmed |
| The child-restart failure proves the same runtime path | Inspect its pipe/shared-process-group helper and the production foreground-group design | Rejected; the test exercises an invalid topology |
| Bash cannot run the foreground-child promotion path | Remove the stale Bash skips and run the real pseudo-terminal promotion tests | Rejected; Bash and zsh both pass |

### Scope-of-fix decision

Scope signals: three or more unrelated subsystems — no; new shared abstraction
— no; public contract change — no; repository boundary — no; broader pattern
— no. Removing the contradictory supervisor escape branch is contained and
preserves the existing child-level terminal gesture.

### Related test-harness finding

The real-tmux clean-exit regression had a separate deterministic hang. Its
test-only `tmux` PATH shim executed `tmux` by name, resolving back to the shim
and recursively `exec`ing itself. The replacement shim resolves the system
`tmux` executable before it prepends its own directory to `PATH`. A focused
shell-level regression timed out before that correction and now confirms that
the shim reports the real tmux version.

### Real-tmux override isolation

The real-tmux tests had two independent setup defects. First, they supplied
the generated template arguments as `ai_name=session_id,
session=test-session`, but wrote the override to `sessions/{session_id}.sh` and
created the tmux session with that same `session_id`. The supervisor therefore
looked up and later killed `test-session`, not the test pane. The missing
override caused it to take the default agent-launch path.

Second, an existing tmux server does not give a new pane the environment from
the `tmux new-session` client process. A direct `show-environment` check after
the failing launch contained no test-specific `XDG_STATE_HOME`; tmux documents
`new-session -e VARIABLE=value` as the way to set it. Consequently the pane
could select the server's state directory and executable path instead of the
test override and fake agent.

The generated CLI launch path had the same environment-propagation gap: it
only supplied terminal metadata to `new-session`, so a long-lived tmux server
could retain an older `PATH` or state directory.

## Fix

The supervisor `SIGINT` trap now only increments its record count, matching
the existing `SIGWINCH` trap. It never calls the `SIGTERM` relay path. The
foreground child retains its existing double-Ctrl+C behavior.

The regression suite replaces the invalid pipe/shared-process-group
child-restart case with a real-tmux test, parameterized for Bash and zsh. It
verifies that two direct supervisor `SIGINT` signals and one direct
supervisor `SIGWINCH` leave the foreground agent untouched, while two terminal
Ctrl+C keystrokes are delivered to the child and cleanly terminate the tmux
session. The existing real pseudo-terminal promotion tests now run for Bash
as well as zsh.

The real-tmux tests now pass their pane environment with tmux `-e` flags and
use the actual tmux session name as the generated template's `session`
argument. The signal topology test runs a minimal session-script override that
executes its fake agent, persists the two-Ctrl+C exit request, and never falls
through to a real agent. The clean-exit test does not exercise terminal
promotion, which is independently covered by the promotion regressions.

Production `tmux new-session` calls now propagate `PATH` and
`XDG_STATE_HOME`, alongside the existing terminal metadata, for both managed
and `--once` launches.

## Verification

1. Frozen direct-signal regression RED before the production edit: 2 failed,
   one per supported shell, at the child-survival assertion.
2. Frozen regression GREEN after the production edit: 2 passed.
3. Real pseudo-terminal child-promotion coverage: 4 passed, including the
   formerly skipped Bash variants.
4. The real-tmux topology regression now passes for Bash and zsh against an
   isolated tmux socket. It verifies two direct `SIGINT` signals plus one
   direct `SIGWINCH` leave the child untouched; two terminal Ctrl+C keystrokes
   reach only the child, restart it once, then end the session.
5. Full reaper module with the configured parallel worker count: 52 passed.
6. The isolated-tmux wrapper regression RED before its correction timed out
   after two seconds; it is GREEN after the correction.
7. Production launch regression RED before the environment propagation patch:
   its captured `tmux new-session` argv lacked `PATH` and `XDG_STATE_HOME`.
   It is GREEN after the patch.

## Lessons learned

Terminal signals and direct process signals are different boundaries. A pipe
and a manually signaled shared process group cannot verify terminal routing;
the regression must use a real terminal-backed tmux pane for that contract.

The Python-level process guard cannot observe a process tmux forks internally.
The focused real-tmux test now has no real-agent fallback, but enabling the
currently disabled process-containment plugin remains a worthwhile follow-up
to detect unowned processes across the complete suite.

## Fix log

| Date | Change | Notes |
|---|---|---|
| 2026-09-02 | Direct supervisor signal contract | Implemented as `implement` / high effort because the generated shell crosses terminal, process-group, and tmux boundaries. |
| 2026-09-02 | Real-tmux override isolation | Implemented as `implement` / xhigh effort after the initial real-tmux harness exposed multiple independent signal, environment, and session-identity boundaries. |
