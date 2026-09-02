---
title: "A remote session can start preflight after a recorded double interrupt"
category: bugs
tags: [session, tmux, signals, direnv, remote]
status: fix-verified-locally
severity: P1
template_version: "bug-1.0.0"
---

# A remote session can start preflight after a recorded double interrupt

**Status:** fix-verified-locally

**Created:** 2026-09-02

## Symptoms

Remote tmux sessions launched with `ai c <index> -R` could remain on repeated
environment-loading output after the documented double-Ctrl+C exit gesture. In
one report, the session exit marker already existed while replacement child
processes continued to start. Separate reports described a tmux dead pane
instead of a clean termination.

## Environment and reproduction

The reported environment was a remote Linux tmux session reached over a remote
terminal transport. The preserved remote host was not reachable from the
investigation environment, so its dead pane could not be captured again.

The deterministic local reproduction uses the real generated session script,
a real subprocess, and the selected bash and zsh shells. It creates the same
persisted exit marker that the second Ctrl+C records, then observes whether a
replacement child invokes a real `direnv` executable before terminating.

Before the fix, both shells invoked `direnv`; the regression assertion failed:

```text
AssertionError: an already-requested exit must not start direnv
```

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

### Hypothesis ledger

| Hypothesis | Check | Result |
|---|---|---|
| The child ignores the persisted exit marker before preflight | Generated-script subprocess regression with a real `direnv` executable | Confirmed |
| tmux dead panes are caused by `remain-on-exit` being enabled | Source inspection shows launch deliberately enables `remain-on-exit`; remote artifact was unreachable | Unresolved for the reported artifact |
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

## Verification

1. Frozen regression RED before the production edit: two failures, one for
   bash and one for zsh, both showing that `direnv` was started.
2. Frozen regression GREEN after the guard: two passes.
3. Disabled the guard without changing the frozen test: the same two expected
   failures returned; restored it and confirmed two passes again.
4. Nearby Ctrl+C and clean-session-exit subprocess tests passed: 6 passed,
   1 skipped.

The isolated real-tmux and remote end-to-end verification rings remain blocked
in this environment: tmux cannot create a socket under the filesystem sandbox,
and the reported remote host name does not resolve here.

## Lessons learned

A durable exit marker is an inter-process command, not a hint for the agent
launch path. Replacement-child loops must honor it before any work that can
block or emit misleading startup output. The regression asserts both the
positive outcome (clean supervisor termination) and the negative constraint
(no `direnv` invocation).

## Fix log

| Date | Change | Notes |
|---|---|---|
| 2026-09-02 | Regression and loop guard | Implemented as `implement` / high effort because process-group signal handling has multiple plausible failure modes. |
