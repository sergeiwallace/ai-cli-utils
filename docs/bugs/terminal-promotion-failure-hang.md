---
title: "A terminal foreground-promotion failure can leave a session supervisor waiting forever"
category: bugs
tags: [session, tmux, terminal, process-group, signals, remote]
status: needs-deterministic-linux-regression
severity: P0
template_version: "bug-1.0.0"
related_docs:
  - docs/bugs/ai-cli-2139-session-exit-leaves-stopped-process.md
  - docs/bugs/supervisor-direct-signal-contract.md
---

# A terminal foreground-promotion failure can leave a session supervisor waiting forever

## Symptoms

Two independent remote sessions displayed no application output, ignored repeated
Ctrl+C, and eventually printed:

```text
ai-cli: could not promote child process group to terminal foreground
```

Both sessions had to be removed from the remote tmux server. The pane captures
are the only preserved launch evidence; per-launch logging was not available.

## Environment and reproduction

The report came from a macOS client connected to a Linux tmux server over a
remote terminal transport. The live incident evidence is preserved in
[`aih-jpnd-2026-09-21-live-linux-repro.txt`](aih-jpnd-2026-09-21-live-linux-repro.txt).

The generated supervisor creates a terminal-backed child wrapper that:

1. creates its own process group;
2. writes its readiness file; and
3. stops itself with `SIGSTOP` until the supervisor makes that group the
   terminal foreground group and sends `SIGCONT`.

The local macOS environment can prove the stopped-process rule directly:

```text
SIGTERM sent to a SIGSTOPped child -> child still running
SIGCONT sent afterward               -> child exits with its queued SIGTERM
```

It cannot reproduce the reported session failure faithfully. The remote host
name is unavailable from this sandbox, and no local Linux container runtime is
running. A focused macOS pty experiment ran the generated supervisor with a pty
that was a terminal but not its controlling terminal. Its real `tcsetpgrp` call
failed and emitted the expected diagnostic after the child reached readiness,
but the macOS zsh supervisor exited rather than blocking in its child wait.
That platform difference means the experiment cannot be retained as a RED
regression for the Linux hang.

## Root cause analysis

The current source has this causal path when foreground promotion itself fails:

```text
tcsetpgrp repeatedly fails after the child wrapper SIGSTOPs
  -> _supervisor_promote_child returns failure
  -> the supervisor sends SIGTERM to the stopped wrapper
  -> the wrapper retains SIGTERM until SIGCONT
  -> _supervisor_wait_for_child waits for that wrapper indefinitely
  -> the pane shows the promotion error and does not return to its shell
```

`src/ai_cli/session_script.py` sends only `SIGTERM` on that path. This is the
same stopped-process rule established in
[`ai-cli-2139-session-exit-leaves-stopped-process.md`](ai-cli-2139-session-exit-leaves-stopped-process.md),
where a stopped process demonstrably retained `SIGTERM` until continued.

The current direct-supervisor `SIGINT` contract records interrupts without
relaying them. While the failed promotion leaves the supervisor in the terminal
foreground group, this explains why additional Ctrl+C input does not provide an
escape while the supervisor is in the retry or wait path. It does not establish
why `tcsetpgrp` failed in the first place.

### Hypothesis ledger

| Hypothesis | Check | Result |
|---|---|---|
| A stopped wrapper retains the failure-path `SIGTERM`, so the supervisor waits forever | Read the generated supervisor and run a real stopped-child signal experiment | Supported; the source has no `SIGCONT` before its wait, and the stopped child remained alive until continued |
| Earlier `SIGTTOU` handling regressed | Read both `tcsetpgrp` call sites and their Python helpers | Rejected for the known prior mechanism; both calls ignore `SIGTTOU` |
| The double-Ctrl+C escape should terminate the stuck supervisor | Read the current supervisor signal contract | Rejected; direct supervisor `SIGINT` is intentionally record-only |
| Two launches share child readiness or terminal process-group state | Inspect generated paths and terminal ownership | Rejected as the direct mechanism: each session creates a distinct readiness file and has its own tmux pane terminal |
| Concurrent transport setup causes `tcsetpgrp` to fail | Need a preserved Linux trace or live concurrent reproduction | Unconfirmed; it may be the trigger, but it does not explain the post-error indefinite wait |

## Scope of fix

The likely cleanup correction is contained within the generated supervisor, but
the root-cause procedure forbids a production edit before a Linux terminal/tmux
regression is confirmed RED. No production edit was attempted.

## Required next reproduction

Run the generated supervisor with Linux zsh and a real pty that is a terminal
but not its controlling terminal. This makes the real foreground-promotion
syscall fail after the wrapper records readiness without mocking `tcsetpgrp` or
requiring tmux or SSH. Bound the generated retry count in the test harness only
so the cleanup branch is reached promptly, then assert all of the following:

- the exact promotion error is emitted;
- the supervisor exits within a bounded interval;
- the stopped child wrapper has exited; and
- an unrelated process remains live.

The test must fail on the current source before any fix. It must not mock
`tcsetpgrp`; it should use the non-controlling pty condition above (or another
real terminal condition that produces the failure). The failure must show that
the supervisor remains live after the promotion diagnostic and that its stopped
child has a pending `SIGTERM`. A Linux tmux test remains a useful additional
end-to-end check, but is not required to establish this cleanup mechanism.

## Verification

- Read the current generated supervisor and prior signal/terminal bug records.
- Confirm the repository working tree was clean before investigation.
- Read the live Linux process evidence: both stopped children had pending
  `SIGTERM`, and both supervisors were blocked in `sigsuspend`.
- Run the direct stopped-child signal experiment: `SIGTERM` alone left the child
  running; `SIGCONT` caused its normal exit.
- Run a real generated-supervisor macOS pty experiment: it emitted the expected
  promotion diagnostic but exited, so it did not reproduce Linux's blocked wait.
- No code or test changes were retained because no valid Linux RED regression
  was available.

## Lessons learned

A child that deliberately stops for terminal handoff needs a cleanup path that
models its stopped state. A successful `kill -TERM` call is not evidence that a
stopped child has exited. Per-launch diagnostics should retain the syscall error
and process-group state so a future incident can distinguish a terminal failure
trigger from the resulting cleanup deadlock.

## Fix log

| Date | Change | Notes |
|---|---|---|
| 2026-09-20 | Investigation recorded | Static causal chain and a real stopped-child signal experiment support the cleanup hypothesis; no production fix because Linux tmux RED evidence is unavailable. |
| 2026-09-21 | Live Linux evidence and macOS pty attempt recorded | Live stopped children had pending `SIGTERM` and Linux supervisors waited in `sigsuspend`. The macOS pty reached the real promotion error but exited, so Iron Gate 5 prevents a production edit until a Linux RED regression is run. |
