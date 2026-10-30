# Relaunch hold parks on a blocked background copy and on stale job records

Tracked as AI-CLI-h330.

## Symptom

After Claude Code's exit dialog moved a session to the background ("Move to
background and exit"), `ai c <name>` with a resume prompt waiting held the
relaunch on every attempt and then parked:

```text
background session <id> is not between turns (state blocked); holding the relaunch ...
gave up after 12 attempts (relaunch.background_hold_max_attempts = 12)
```

It also parked hours later, after the background copy had been retired and its
daemon had exited.

## Root cause

Two separate faults:

1. `background_attach.py` treated only `done` as between turns, and read
   `blocked` as "waiting on a dialog". In Claude Code 2.1.311, `blocked` is
   the state of a background copy that finished its turn and waits on the
   user. Its job record carries a `needs` text. A copy holding only a shell
   sits in `blocked` too. So every prompted relaunch onto such a copy held
   until it parked.
2. `claude agents --json` reads the job records under `~/.claude/jobs/`. The
   daemon's idle retire of a `blocked` job does not write a terminal state.
   The record keeps `state: blocked` after its worker and the daemon are both
   gone, and the list still shows it as a live background copy. Seen on two
   hosts: one record settled as `done` in `~/.claude/daemon.log` a month earlier
   still read `blocked`, and the roster's supervisor pid was dead.

## Fix

- `blocked` joins `done` as a between-turns state. Typing still waits for the
  input box. `_composer_drawn` now also refuses a pane that shows a numbered
  choice (`❯ 1. ...`), because a blocked copy can be showing one.
- `list_live_background_sessions` drops a row when its job record names the
  `daemon` backend and `~/.claude/daemon/roster.json` shows no live worker for
  it: the supervisor pid is dead, or `workers.<id>.pid` is missing or dead.
  `CLAUDE_CONFIG_DIR` is honoured. An unreadable roster or job record leaves
  the row live, so the hold stays the safe default.

Name collisions (`<name> (2)`) were already matched by `is_copy_title`.

Follow-up: with agent view turned off (the `disableAgentView` setting or
`CLAUDE_CODE_DISABLE_AGENT_VIEW`), Claude Code runs no background sessions, and
`claude agents --json` exits 1 with `'claude agents --json' is disabled by ...`
(measured on 2.1.312). That refusal now reads as an empty list. Any other
failure is still an unreadable list and still holds a relaunch that a resume
follows.

## Verification

- Frozen RED: four new tests failed on the unfixed code (a blocked copy with a
  live worker held; a blocked or working record with no live worker held; a
  blocked copy was never typed into).
- The numbered-dialog test fails when its guard is removed.
- `tests/test_background_attach.py`: 67 passed; 70 with the agent-view-off
  follow-up, whose two refusal cases failed before the change.
