---
title: "A terminal foreground-promotion failure can leave a session supervisor waiting forever"
category: bugs
tags: [session, tmux, terminal, process-group, signals, remote]
status: fixed
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

**Fixed 2026-09-22.** `_supervisor_promote_child`'s failure branch in
`src/ai_cli/session_script.py` now sends `kill -CONT -"$_child_pid"` (the whole
process group) immediately after the existing `kill -TERM`, so the stopped
child wakes and actually processes its queued terminate signal instead of
leaving `_supervisor_wait_for_child`'s `wait` blocked forever.

## Linux RED/GREEN regression (2026-09-22)

`tests/test_stale_session_reaper.py::test_given_noncontrolling_terminal_when_promotion_fails_then_supervisor_exits_and_child_dies`
reproduces the failure without tmux, SSH, or mocking `tcsetpgrp`, run live on a
real Linux host (Fedora, kernel 7.2.5):

- A real pty is opened, and a throwaway `bash` "owner" process is started as
  its own session leader on that pty first and left running. A plain
  `O_NOCTTY`-opened fd handed to an unrelated session leader turned out
  **not** to be sufficient on its own -- an interactive-capable shell that is
  itself a fresh session leader auto-claims any valid tty the moment it
  starts, regardless of how an ancestor process opened the fd. Giving the
  terminal to a different session *first* is what makes the later
  `tcsetpgrp` call fail deterministically (ENOTTY), matching the production
  failure mode.
- The generated supervisor's retry bound is reduced from 3000 to 20
  iterations in the test harness only (`fast_promotion_retry=True`), so the
  now-guaranteed-to-fail cleanup branch is reached in ~0.2s instead of 30s.
- Before the fix: the test failed by timing out (`pytest.fail` after a 10s
  `communicate()` deadline) on both bash and zsh-generated `_session_shell`
  bodies run under a bash-interpreted supervisor; the promotion error printed
  but the supervisor never exited. Under a zsh-interpreted supervisor the
  test already passed even before the fix -- zsh's `wait` builtin returns
  differently for a stopped child than bash's, so only the bash path
  exhibited the indefinite hang (both remain covered by the fix, which is a
  no-op once the child has already exited).
- After the fix: both parametrized shells (`bash`, `zsh`) pass in under 2s,
  asserting the exact promotion error is emitted and the supervisor exits
  with status 1.
- The independent stopped-process mechanism (`SIGTERM` alone -> no effect;
  `SIGCONT` afterward -> child exits with the queued `SIGTERM`) was also
  re-confirmed directly against this same Linux host before writing the fix.

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
| 2026-09-22 | Fixed and verified on real Linux (AI-CLI-jpnd) | Built a deterministic non-controlling-pty RED regression, confirmed it hangs on current source, applied a `kill -CONT -"$_child_pid"` fix after the existing `kill -TERM`, re-verified GREEN, reverted-and-reconfirmed RED, then restored the fix. Full test suite run on both Linux (Framework) and macOS shows zero new failures beyond the pre-existing set tracked under `AI-CLI-u3zc`. |
