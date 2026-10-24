---
title: "Relaunch repeatedly holds on ambiguous background copies"
category: bugs
tags: [bug, relaunch, background-session, claude-code]
status: fixed
severity: P1
template_version: "bug-1.0.0"
---

# Relaunch repeatedly holds on ambiguous background copies

## Symptoms

After a client chose “Move to background and exit,” its supervisor tried to
relaunch the conversation. When an older background copy had the same session
name and directory, the resolver printed the same hold detail every five
seconds and did not identify the new idle copy. A configured time limit
eventually parked the relaunch, but the repeated hold could last five minutes.

## Environment

The failure was reproduced on Linux at base revision `51240da` through the
generated POSIX session script and the real internal resolver entry point. A
PATH-scoped `claude` fixture returned two background rows:

- `bbbb2222`: an older row in state `working`, with no PID.
- `aaaa1111`: a row created one second before relaunch resolution, with status
  `idle`, state `blocked`, and a PID.

No live session, daemon, terminal, or process was stopped during reproduction.

## Reproduction

The regression test
`test_given_working_and_recent_idle_copies_when_relaunching_then_loop_stops_at_configured_attempts`
runs the generated relaunch loop with `background_hold_max_attempts = 2`. On
the unfixed base, the option was unknown and the loop made four resolver calls
before the older elapsed-time limit ended it:

```text
assert len(agents_calls) == 2
AssertionError: assert 4 == 2
```

Two companion resolver tests showed that the recent idle copy was not adopted
when all siblings were idle, and that a failed `claude stop` did not enter the
bounded exit with per-copy remedies.

## Root Cause Analysis

The trigger was more than one live background row matching the same name and
directory. The resolver reduced all such rows to an ID list and always returned
a hold, discarding the `startedAt`, `status`, and `pid` fields that distinguish
the exit-created row. The generated shell loop retried that decision every five
seconds and printed the resolver's stderr on every attempt. Its only normal
bound was elapsed time, so slow or stalled clocks could extend the loop and the
operator received no exact per-copy recovery commands.

| Hypothesis | Check | Result |
| --- | --- | --- |
| A stopped row was counted as live | Included stopped and done fixtures in the existing resolver suite | Rejected: stopped rows are filtered; done rows are intentionally live and idle |
| The external resolver selected the wrong row | Reproduced with no configured external resolver | Rejected: ambiguity arises in built-in name/directory matching |
| Multiple matches erase useful provenance | Supplied a recent idle PID row beside older rows | Confirmed: the unfixed resolver returned status 75 without inspecting those fields |

## Scope Decision

The fix stays within the existing relaunch component and its internal CLI
contract. It adds no public command, dependency, repository boundary, or shared
abstraction. The change is reversible and limited to classification, retry
configuration, and generated-loop reporting, so a contained bug fix is the
lower-risk choice.

## Fix

The resolver now recognizes exactly one exit-created candidate only when it has
an idle status, a positive PID, and a millisecond start time inside a configured
30-second relaunch window. It adopts that candidate only when every sibling is
strictly between turns. A working, blocked, or unknown sibling keeps the guard
closed and is never stopped or signalled.

Holds now also stop at `relaunch.background_hold_max_attempts`, default 12 and
read on every resolver call. Intermediate details are suppressed by the
generated loop. The terminal report prints one exact `claude attach <id>` and
`claude stop <id>` remedy for each matching copy. Stop failures use the same
bounded path.

## Verification

- Frozen RED tests: three expected failures on `51240da`, including
  `AssertionError: assert 4 == 2` at the real subprocess boundary.
- Focused GREEN: `uv run pytest tests/test_background_attach.py tests/test_main.py -q`
  — `175 passed in 1.68s`.
- Focused lint: `uv run ruff check` on all touched source and test files —
  `All checks passed!`.
- The repository-level gate is recorded after the final documentation commit.

## Lessons Learned

The prior guard correctly prioritized “never resume beside a possible live
copy,” but treated ambiguity as static. Boundary tests need to preserve the
full agents-row shape and run the generated retry loop, not only call the
single-attempt resolver. A retry guard also needs both time and attempt bounds,
because elapsed time alone does not constrain the number of side effects or
messages.

## Fix Log

| Revision | Result |
| --- | --- |
| `ce58dba` | Added and froze real-boundary and resolver RED coverage |
| `eac3d87` | Added recent-copy classification, configurable attempt bound, one-time remedies, and preserved working-copy safety |
