---
title: "Claude Code launch fails when stdin is reopened through /dev/tty"
category: bug
tags: [bug, session-launch, macos, terminal, kqueue]
status: fixed
severity: P1
template_version: "bug-1.0.0"
---

# Claude Code launch fails when stdin is reopened through /dev/tty

## Summary

On macOS, `ai c 1` could repeatedly crash Claude Code with `EINVAL: invalid
argument, kqueue`. After three exits, the launcher's consecutive-failure circuit
breaker stopped retrying, so the requested session could not be launched or
resumed.

## Environment and reproduction

The failure was observed with Bun 1.4.3 on macOS arm64 after commit `6f0127b`
changed supervised agents to retain terminal stdin. Running `ai c 1` from a
registered repository printed the Bun crash and then the launcher's
three-consecutive-exits diagnostic.

The implementation worker's sandbox cannot create tmux sockets or access a real
TTY. The live symptom and causal boundary were therefore confirmed externally;
the repository regression test covers the generated shell contract.

## Root cause analysis

The generated `run_agent` function backgrounded Claude Code with stdin reopened
from the literal controlling-terminal alias:

```bash
"$@" </dev/tty &
```

Claude Code is a Bun-compiled executable. On macOS, `ttyname_r()` on a descriptor
opened through `/dev/tty` can return that alias again, and kqueue rejects polling
that device with `EINVAL`. The resulting stdin stream failure surfaced from
`pull()`, terminated Claude Code, and let the supervisor's new circuit breaker
turn three identical exits into the final launcher diagnostic.

[Bun PR #41504](https://github.com/oven-sh/bun/pull/41504) documents the same
kernel boundary: a pseudo-terminal slave opened by its resolved device path is
pollable, while the `/dev/tty` alias is not. That upstream change concerns Bun's
own non-stdio file polling; this launcher created the problematic stdin
descriptor before Bun started, so the caller must supply the resolved device.

### Hypothesis ledger

| Hypothesis | Evidence | Result |
| --- | --- | --- |
| The consecutive-exit counter caused the crash | It only observes exits after Claude Code emits the Bun error. | Rejected; it exposes and bounds the failure. |
| Repeated direnv evaluation exhausted kqueue resources | The environment loaded before each visible crash, but the Bun stack identifies stdin polling and the alias behavior is independently documented. | Rejected. |
| Reopening stdin through `/dev/tty` supplied an unpollable descriptor | The regression began with that redirect, the stack failed in stream `pull()`, and Bun's macOS analysis describes the matching `EINVAL`. | Confirmed. |

## Prior fix attempts

The first investigation correctly stopped without a patch because its sandbox
could not reproduce the real tmux/TTY boundary. No speculative fix was applied.

## Fix

`run_agent` now calls `tty` before backgrounding and, when it returns a readable
character device, redirects stdin from that resolved path. The existing
`/dev/tty` behavior remains the defensive fallback when resolution or validation
fails; non-terminal launches continue to duplicate fd 0.

This is a contained, reversible change to one generated launch boundary. It
does not change a public API, introduce a shared abstraction, or cross a
repository boundary, so no broader redesign is warranted.

## Verification

The frozen regression
`test_given_terminal_stdin_when_agent_is_backgrounded_then_resolved_device_path_is_preferred`
generates the real `run_agent` body and requires terminal resolution to precede
the resolved-path redirect and the literal fallback.

On the unfixed template it failed with:

```text
AssertionError: run_agent did not resolve the controlling terminal's device path
1 failed in 0.07s
```

After the fix it passed:

```text
1 passed in 0.04s
```

Nearby launch coverage passed with `98 passed, 5 deselected in 1.63s`.
Repository lint and formatting gates passed with `All checks passed!` and
`297 files already formatted`. The full suite reached `2833 passed`, but 47
tests failed because the sandbox denies tmux/Unix/network sockets, writes below
the real user state directory, and several unrelated pre-existing portability
and public-repository hygiene checks. Its final line was:

```text
47 failed, 2833 passed, 30 skipped, 5 warnings in 56.00s
```

A live `ai c 1` launch was not run in the implementation sandbox and remains a
required external verification step.

## Lessons learned

Terminal retention tests must cover not only whether stdin is a TTY, but also
which device path was used to open the descriptor. A generic terminal alias and
its resolved pseudo-terminal slave are not interchangeable polling targets on
macOS.

## Fix log

| Date | Commit | Notes |
| --- | --- | --- |
| 2026-09-14 | pending | Implement role, medium effort: resolve the terminal device before backgrounding and add frozen generator-level regression coverage. |
