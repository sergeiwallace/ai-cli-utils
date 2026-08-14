---
title: "Exiting a session can leave its process stopped, not dead, so the launcher refuses to resume the name"
category: bug
tags: [bug, session-launch, session-registry, process-lifecycle, linux, proc]
status: fixed
template_version: "bug-1.0.0"
---

# Exiting a session can leave its process stopped, not dead

**Status:** fixed — reproduced, root-caused, regression suite frozen red before the fix, hard gate green

**Task:** AI-CLI-2139

**Created:** 2026-08-14

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

<!-- doc:region name="summary" kind="replaceable" -->

## Summary

Exiting a Claude Code session does not reliably end its process. The process can survive in
state `T` (stopped): still listed in `/proc`, still holding its pid and its open files, and
never resuming on its own. Every pid-liveness predicate answers "alive" for it.

The launcher reads `~/.claude/sessions/<pid>.json` to decide whether a session name is in use.
Because a stopped process satisfies both of that check's conditions — the pid exists, and its
start time matches the record — the launcher classified an abandoned session as live, printed
"still running", dropped `--continue`, and started a new, differently-named session instead of
resuming. Nothing ever reaped the stopped process, so the name stayed unusable.

The distinction pid existence cannot make is exactly the one that matters: *alive and in use*
versus *alive but abandoned*. The `/proc` state field can make it.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

```yaml
reproduction:
  revision: 7973574 (branch wt/aicli-exit-fix, before the fix)
  environment: Linux 6.12 container, Python 3.13, editable install (the checkout is what runs)
  command_or_steps: |
    1. Spawn a process and SIGSTOP it, so /proc/<pid>/stat reports state T.
    2. Write ~/.claude/sessions/<pid>.json naming that pid, with procStart read
       from field 22 of /proc/<pid>/stat.
    3. Ask the launcher whether that session is live.
  expected: not live — the process is stopped, so the name is free to resume
  observed: live, and the process is left stopped forever
  exit_status: "_cc_session_is_live(...) -> (True, <pid>)"
  reproducibility: deterministic
  baseline_failures: none — the suite was green before this work (2332 passed, 6 skipped)
  evidence: |
    state before SIGSTOP: S
    state after  SIGSTOP: T
    _pid_is_live          -> True
    _cc_record_liveness   -> live
    live_sessions         -> [(89914, 'myproject-3')]
```

The originally reported observation, on a live session rather than a spawned stand-in, showed
the same state and the reason the terminate looked successful:

```text
kill -TERM <pid>   -> rc 0
+4s                -> /proc/<pid> still present, state field = T
kill -KILL <pid>   -> gone
```

`rc 0` means the signal was *queued*, not handled. A stopped process does not run, so it never
acts on a `SIGTERM` until something continues it. Reading the kill's return code as proof of
death is the trap; the absence of `/proc/<pid>` is the only honest check.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

`_cc_record_liveness` (`src/ai_cli/main.py`) asked two questions — does the pid exist, and does
its start time match the record — and treated "yes, yes" as "a session is using this name". Both
are true of a stopped process, so a state the launcher never modelled was folded into `live`.

**Causal chain:**

```text
a session exits, but its process is left in state T (stopped)
  → /proc/<pid> is fully present and its starttime still matches the record
  → _cc_record_liveness returns "live"
  → _cc_session_is_live returns (True, pid)
  → _bare_engine_command / `ai internal resolve-continue-target` print
    "still running" and drop --continue
  → `ai c <n>` starts a fresh session under a new name; the stopped process is
    never reaped, so the name stays reserved indefinitely
```

Two smaller contributors sat behind the same missing distinction:

- **Nothing ends the process.** The registry check only ever *read*. Because a stopped process
  cannot end itself, no amount of correct classification frees the name unless something
  escalates properly — and a bare `SIGTERM` provably does not.
- **The record outlives the process.** `~/.claude/sessions/<pid>.json` is pruned only for a
  provably dead pid, so an abandoned-but-present process left its record to be re-interpreted
  on every later launch.

This is not the defect fixed by AI-CLI-cc-session-live-mvht. That one made the check verify pid
liveness plus `procStart`, which correctly handles an *abandoned record for a dead pid*. Here
the pid is genuinely alive, so that predicate is right and its conclusion is still wrong.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="rejected_hypotheses" kind="replaceable" -->

## Rejected Hypotheses

| Hypothesis | Predicted observation | Check performed | Result |
|---|---|---|---|
| H1: a present-but-stopped process is classified `live`, which suppresses `--continue` | `_cc_record_liveness` returns `live` for a real `SIGSTOP`ped child whose `procStart` matches | Spawned a child, `SIGSTOP`, read `/proc/<pid>/stat`, called the predicate | **Confirmed** — `live`, and `_cc_session_is_live` returned `(True, pid)` |
| H2: the record is stale for a pid that is already dead (the mvht defect) | `/proc/<pid>` absent | Read `/proc/<pid>` directly: present, state `T`, four seconds after the `SIGTERM` | Rejected — the pid is alive, so the existing `procStart` check cannot fire |
| H3: the exit path relies on terminal/tmux teardown to reap the child | ending the pane would take the process with it | `tmux` is unusable on the reporting host (missing `libutempter.so.0`), yet the defect occurred; job-control stop is not something a pane teardown produces | Rejected — no tmux-managed lifetime exists there |
| H4: the terminate itself failed and the return code would have shown it | `kill -TERM` returns non-zero | `kill -TERM` returned 0 and the process stayed in `T`; a queued signal is not a delivered one | Rejected — the kill "succeeded" and changed nothing |
| H5: a `SIGTERM` plus a longer wait is enough | the process exits once given time | Sent `SIGTERM` to a stopped process and waited: still `T`. Sent `SIGCONT`: it exited immediately | Rejected — the escalation must continue the process, not wait longer |

<!-- /doc:region name="rejected_hypotheses" -->

<!-- doc:region name="scope_of_fix" kind="replaceable" -->

## Scope of Fix

Scope signals, assessed after the root-cause gate: three or more unrelated files or subsystems —
**no** (one cohesive component: the launcher's session-registry liveness check in
`src/ai_cli/main.py`). New shared abstraction or ownership boundary — **no**. Public
contract/API change — **no** (all four helpers are private; the two entry points keep their
signatures and return types). Repository or architectural boundary — **no**. Broader pattern
flagged elsewhere — **partly**: `session_adopt.live_sessions` shares the same pid-existence
predicate, and is deliberately left alone (see [Lessons Learned](#lessons-learned)).

Threshold not met, so the narrow causal fix stays in this bug-fix task: it is reversible (one
file, one commit), its blast radius is one launch decision, and the code region is stable rather
than churning.

<!-- /doc:region name="scope_of_fix" -->

<!-- doc:region name="fix" kind="replaceable" -->

## Fix

All in `src/ai_cli/main.py`, commit `edebffb`:

- **`_proc_state(pid, proc_dir)`** — reads field 3 (`state`) of `/proc/<pid>/stat`, counted from
  after the *last* `)` so an executable name containing spaces and parens still parses.
- **`_cc_record_liveness`** — adds an `"abandoned"` verdict for `T` (job-control stop), `t`
  (tracing stop) and `Z`/`X`/`x` (exited, unreaped). Checked *before* `procStart`, so a record
  that carries no start time is still covered. `live`, `gone` and `unproven` are unchanged.
- **`_pid_has_ended(pid, proc_dir)`** — the verification oracle: `/proc/<pid>` absent, or present
  only as a zombie. Never a signal's return code. A zombie counts as ended because it has
  already exited and holds nothing open; the outstanding `wait()` belongs to a parent the
  launcher cannot act for, and a parent that is itself stopped will never perform it.
- **`_end_session_process(pid)`** — `SIGTERM`, `SIGCONT`, bounded wait, `SIGKILL`, aimed at the
  process **group** so a wrapper's children are reaped rather than orphaned. The `SIGCONT` is
  part of the mechanism, not a belt-and-braces extra. A group that is the launcher's own process
  group is signalled by pid instead, because a group signal there would kill the launcher and
  everything sharing its job.
- **`_reclaim_abandoned_cc_session(entry, record)`** — runs for the session being resumed only
  (the registry walk visits every record, and a resume must not become a fleet-wide reaper),
  signals only when `procStart` proves which process the pid is (a recycled pid must never be
  killed on the strength of a stale record), prunes the record once the process is gone, and
  always reports what it found and what it did.

Why this targets the cause rather than the symptom: the launcher's wrong decision came from a
predicate that could not express "present but not running". The fix gives it that verdict and
then makes the abandoned state terminal, so the name is free on the next launch and the operator
is told why. It is not "always `SIGKILL`" (the escalation starts at `SIGTERM` and only a proven
non-exit escalates), not a suppressed check (a genuinely running session still blocks the name
and is never signalled), and not a flag.

<!-- /doc:region name="fix" -->

<!-- doc:region name="verification" kind="replaceable" -->

## Verification

Frozen regression suite: `tests/test_cc_session_stopped.py`, 10 tests, committed red at
`80f02cc` before any production edit. Every assertion about a stopped process drives a real
`SIGSTOP`ped child; `/proc` and the predicate are never mocked, because that is the boundary the
defect lives on. The sleepers are orphaned through a double fork so `init` reaps them and the
absence of `/proc/<pid>` is a real answer rather than a zombie artefact of the test runner.

Widening rings, all run with the venv interpreter from the worktree:

| Ring | Command | Result |
|---|---|---|
| 1 frozen test, unfixed code | `pytest tests/test_cc_session_stopped.py` | 7 failed, 3 passed — `assert (True, 138821) == (False, None)` |
| 2 frozen test, fixed code | same | 10 passed |
| 3 fix reverted (`git stash push -- src/ai_cli/main.py`) | same | 7 failed, 3 passed again — the test is coupled to the fix |
| 4 fix restored | same | 10 passed |
| 5 nearby suites | `pytest tests/test_bare_worktree.py tests/test_main.py tests/test_session.py tests/test_session_adopt.py tests/test_session_audit.py tests/test_cli.py tests/test_cli_dispatch.py tests/test_session_launch_integration.py` | 641 passed, 5 skipped |
| 6 hard gate | `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest` (`-n auto` from `addopts`) | ruff clean, 95 files formatted, **2332 passed, 6 skipped** |

The three tests that pass in both directions are the negative constraints, and they are the
reason "never report live" cannot be satisfied by a predicate that always answers False: a
genuinely running session is still reported live *and* is not terminated, the `/proc` state
parse still recognises a running process, and another session's stopped process is left running
with its record intact.

<!-- /doc:region name="verification" -->

<!-- doc:region name="lessons_learned" kind="replaceable" -->

## Lessons Learned

- **A pid-liveness predicate answers a narrower question than its callers assume.** "The pid
  exists" is not "the session is in use": it is also true for a stopped process and for one
  whose terminal is gone. Two fixes in this area (mvht, then this one) both came from a caller
  reading more into that predicate than it measures.
- **A kill's return code is not evidence of death.** `kill -TERM` returns 0 for a stopped
  process it does not end. Any termination path needs a `/proc`-level oracle and a bounded
  escalation; the [exit procedure](../procedures/exiting-a-cc-session.md) now states that check
  instead of assuming the exit worked.
- **Why the existing tests did not catch it.** The registry-liveness tests either used a
  synthetic `/proc` (whose `stat` fixtures all wrote state `S`) or a real *running* pid. No test
  had ever produced a process in any other present state, so the untested state was also the
  unmodelled one. The frozen suite now spawns and stops a real process.
- **Deliberately not changed:** `session_adopt.live_sessions` uses the same pid-existence
  predicate, but it guards an *adoption*, which moves files a stopped process still holds open
  and could resume appending to the moment anything continues it. Refusing there is the safe
  answer, and the asymmetry matches the existing note in `_cc_record_liveness` about the
  worktree probe failing closed while this one fails open. Do not harmonise them without
  re-deciding that trade.

<!-- /doc:region name="lessons_learned" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
|------|--------|-------|
| 2026-08-14 | `80f02cc` | Frozen regression suite committed RED (7 failed, 3 passed) before any production edit. |
| 2026-08-14 | `edebffb` | Causal fix: `"abandoned"` verdict for present-but-not-running states, scoped reclamation with `SIGTERM`/`SIGCONT`/bounded wait/`SIGKILL` on the process group, `/proc`-verified, record pruned. Frozen suite 10/10; hard gate 2332 passed. |

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

## Appendix: Evidence

The escalation, measured on an orphaned child in its own process group before the fix was
written — this is what made `SIGCONT` part of the mechanism rather than a guess:

```text
grandchild 105309 state S ppid 1 pgid 105309 mypgid 105300
after SIGSTOP state T
after SIGTERM (still stopped) present True state T
after SIGCONT present False
```

Line 3 is the reported bug in miniature: the `SIGTERM` was accepted, and the process was still
there in state `T`. Line 4 is the fix's mechanism: continuing the process let it act on the
signal it had been holding, and it exited without needing `SIGKILL`.

<!-- /doc:region name="appendix_evidence" -->
