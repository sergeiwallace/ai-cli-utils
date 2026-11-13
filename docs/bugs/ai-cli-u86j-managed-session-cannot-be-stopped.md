---
title: "[AI-CLI-u86j] A managed session cannot be stopped, so a migration cannot finish"
category: bugs
tags: [bug, session, supervisor, migrate-session]
status: fix-deployed
severity: P1
task: AI-CLI-u86j
---

# [AI-CLI-u86j] A managed session cannot be stopped, so a migration cannot finish

**Status:** fix-deployed

**Severity:** P1

**Created:** 2026-11-11

**Task:** `AI-CLI-u86j`

## Founding Ask Coverage

N/A: filed from a migration that could not complete. No founding-ask document exists for it.

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

`ai migrate-session OLD NEW` and `--finish` refuse while the session runs, as they
must. The session cannot be made to stop:

- `/exit` inside the session ends the agent, and its wrapper starts a new one within
  seconds. The migration is still refused, now naming the new pid.
- The refusal suggested `tmux kill-session`, which ends the session without the
  wrapper recording a manual exit.
- No `ai` command stopped another session. Typing `/exit` into the pane from another
  terminal does not work either: a read-only tmux client cannot send keys.

Each migration left half done needed a person to stop the session by hand.

## Environment

Any session started by `ai c` (or `ai g`, `ai p`, `ai cx`) in tmux, on Linux or macOS.

## Reproduction Steps

1. Start a session with `ai c myproject-box-1` and let the agent run for more than
   3 seconds.
2. Exit the agent (`/exit`).
3. Observe: the wrapper starts a new agent with a new pid, and
   `ai migrate-session myproject-box-1 NEW -y` is still refused.

`tests/test_sessions_stop.py` reproduces this without a real agent. It starts the
wrapper `session_script.py` generates, with a stub agent, and sends the stub SIGTERM
without writing the stop marker: the wrapper relaunches the stub under a new pid.

## Root Cause Analysis

The generated wrapper treats an agent exit as a restart. It stops on three things only:

- the marker `$XDG_STATE_HOME/ai-cli-utils/session-int-exit-<tmux session>`, checked
  after each agent exit (`session_script.py:754`, `:1026`) and before each relaunch
  (`:981`, `:1297`, `:1579`);
- exit status 77 (a second Ctrl+C within 3 seconds);
- the end of its tmux session.

Each new wrapper deletes the marker when it starts (`session_script.py:430`), so a
marker written before the session starts does nothing. The only code that wrote the
marker was `ai sessions _begin`'s own SIGINT handler, for its own session
(`session_commands.py:71` before this fix). Nothing wrote it for another session, and
`_refuse_live` (`session_migrate.py:206-230` before this fix) pointed at
`tmux kill-session` instead.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11-11 | `/exit` inside the session | The wrapper relaunched the agent within about 10 s |
| 2 | 2026-11-11 | Typing `/exit` into the pane from another tmux client | Refused: the client is read-only |
| 3 | 2026-11-11 | Writing the marker by hand while the wrapper ran, then SIGTERM to the one agent pid | Worked: the wrapper recorded `manual_exit` and did not relaunch. This fix makes that a command |

## Fix

`ai sessions stop <name>` (`session_commands.stop_session` from Python):

1. For each tmux session hosting `<name>` (`c-<name>`, `c-r-<name>` and the other
   engines, the same list `_refuse_live` checks) whose pane process is running, it
   writes the stop marker.
2. It sends SIGTERM to each Claude Code process recorded under `<name>`, and to each
   Claude Code process running under a wrapper it marked whatever name its record
   carries, by its pid, and only when the record's `procStart` matches the process
   holding that pid. A record that cannot prove its pid is left unsignalled and
   reported.
3. It waits up to `-t/--timeout` seconds (default 30) until no wrapper, agent or tmux
   session is left in the slot. It reports what it stopped, and exits 1 naming
   whatever still runs.

The wrapper then exits through its normal manual-exit path, so the session is
recorded as `manual_exit` and boot relaunch leaves it down. Both live-session
refusals in `_refuse_live` now name `ai sessions stop <name>`.

## Verification

- [x] `tests/test_sessions_stop.py::test_given_a_running_managed_session_when_stopped_then_its_agent_gets_term_and_is_not_relaunched`:
  the stub agent ends, the wrapper exits 0, and the stub was launched once.
- [x] With the marker write removed, the same test fails: the wrapper relaunches the
  stub under a new pid and the command exits 1 naming the wrapper, the new agent and
  the tmux session.
- [x] A name with nothing running reports that and exits 0.
- [x] `tests/test_session_migrate.py::test_given_a_running_session_when_migrating_then_the_refusal_names_the_stop_verb`.

## Follow-up: a slot agent whose record names another session (2026-11-12)

**Symptom.** Measured on 2026-11-11 after `ai migrate-session` renamed a session: the
Claude Code process in that slot's tmux pane still had a record naming the old
session. `ai sessions stop NEW` printed `stop marker written for its supervisor`,
then `still running after 30s: supervisor pid <pid>`, and exited 1. It printed no
`sent SIGTERM` line and did not name the agent. The agent had to exit by itself;
the marker did stop the wrapper relaunching it.

**Cause.** `stop_session` chose which Claude Code processes to signal, and which to
report as still running, only by `record.name == NAME`. A record written before the
rename carried the old name, so the agent was neither signalled nor reported.

**Rejected hypothesis.** The marker write failing: it was written, and the wrapper
did not relaunch.

**Fix.** After marking, `stop_session` reads the descendants of each marked
supervisor (`_slot_processes`, kept only while the supervisor is still the process
whose identity it captured). A live Claude Code record whose pid is one of them is
treated like a record named NAME: signalled only when its `procStart` proves the
pid, otherwise reported as `its record cannot prove the pid; not signalled`.

**Tests.** In `tests/test_sessions_stop.py`, both RED at the unfixed code with the
live failure's exact output, GREEN after:

- `test_given_a_slot_agent_whose_record_names_another_session_when_stopped_then_it_gets_term_and_is_not_relaunched`
- `test_given_a_slot_agent_whose_record_cannot_prove_its_pid_when_stopped_then_it_is_named_and_not_signalled`

**Lesson.** A process found by a name it records is found only while that name is
current. When the verb already holds the slot's supervisor, the process tree is the
identity that survives a rename.

## Lessons Learned

A refusal is only useful if the action it asks for is possible. This one asked the
operator to stop a session that the tool itself would restart. A refusal message
should name a command that removes the reason for the refusal.

A regression test for the restart path must let the stub agent run for more than
3 seconds. An agent that exits sooner ends the session on its own ("exited too
quickly"), and the test then passes without the stop marker.
