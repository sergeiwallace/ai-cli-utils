---
title: "A pending migrate-session finish warns on every launch and never completes while NEW runs"
category: bug
tags: [bug, sessions, migration, supervisor, liveness]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A pending migrate-session finish warns on every launch and never completes while NEW runs

## Founding Ask Coverage

The ask: a pending finish either completes without user action at a safe point, or is reported
once with a clear manual step rather than on every launch. Both halves are built. A finish now
runs while NEW is live and completes whenever nothing left touches NEW's open files. When
something does, it is deferred, recorded and warned about once, with the stop command and the
finish command. Nothing in the ask was narrowed.

**Status:** resolved

**Severity:** P2

**Created:** 2026-11-16

## Table of Contents

- [Symptoms](#symptoms)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Decision](#decision)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

While a migration OLD -> NEW was pending, every supervisor restart of NEW logged
`migration OLD -> NEW not finished: session NEW (pid N, ...) is still running in the slot being
finished -- its transcript and task files are open. Stop it first ...`. Every `ai c NEW` that
attached to the running session warned that the migration was partial. A session that its
supervisor restarts rather than exits never satisfies "stop it first", so the warning repeated
for as long as it ran and the finish never ran. A writing `ai migrate-session NEW --finish`
refused as a whole, although its plan for one real pair was mostly venv back-pointer rewrites,
resume and poller key rewrites and held-output renames, none of which touches the open
transcript or task files.

## Reproduction Steps

1. Migrate a slot OLD -> NEW and drop the `finished` row from the ledger, so the pair is pending.
2. Seed OLD-keyed launch logs (safe to carry while NEW runs), a transcript in NEW's project
   directory still titled OLD, and a task left in OLD's namespace by an interrupted task step.
3. Write a Claude Code session record naming NEW whose pid is live.
4. Run `ai migrate-session OLD NEW --finish -y`: it exits 2 and writes nothing. Run
   `ai internal finish-migrations c-NEW NEW` twice: both warn, and nothing moves.

`tests/test_session_migrate_finish_while_live.py` does each of these on fixture homes.

## Root Cause Analysis

1. `finish_session` (`src/ai_cli/session_migrate.py:2303` at the base commit) calls
   `_refuse_live(old, new, ...)`, which raises `LiveSessionError` (`:252`) when any live
   process is named OLD or NEW or runs inside either slot, and (`:260`) when any tmux session
   hosts either name. A writing run re-raises it before its first write (`:2310`).
2. The guard is one all-or-nothing check for the whole finish, although only some of its steps
   write files the live session holds open.
3. The restart finish (`src/ai_cli/main.py:3771`) and the attaching launch (`:3662`, read-only
   re-plan) run on every restart and launch, and nothing records that the same refusal was
   already reported, so each one warned again.

The suspected root cause in the issue, that the launcher re-checks only at launch, was half
right: the restart path already re-checks between agents. It still refused there because a
live Claude Code process named NEW (a backgrounded conversation, or one the supervisor
attaches to) counts as live whatever tmux session the wrapper runs in.

## Decision

**Question:** how should a pending finish make progress while NEW keeps running?

| Option | Pros | Cons |
| --- | --- | --- |
| A. Run every step that does not write NEW's own open files, defer the rest, record the deferral and warn once | Most of a real plan lands at once. The guard on transcripts and tasks is kept as it was. A later finish after NEW stops does the rest | The parts table gains a `deferred` status; callers must tell a deferred-only partial from other partials |
| B. Finish only at the supervisor's between-agents point | No new status | Already built, and it still refuses while a NEW process lives on across restarts, which is the reported case |
| C. Keep refusing, only stop repeating the warning | Small | The finish still never runs, which fails the first half of the ask |

**Recommendation:** A, as the driving session proposed. The deferred parts are the ones that
write files the running session holds open or writes: its project directory and transcript
titles, its sub-agent and background-job records, and its task namespace. Memory and held-output
files are renamed in place and the memory index is replaced only if unchanged, so they run. A
live OLD still refuses the whole finish, since OLD's own wrapper and files would move from
under it.

Decided 2026-11-16 by the fixing agent (Claude Opus 5.5).

## Fix

- `src/ai_cli/session_migrate.py`: `finish_session` checks OLD first, which still refuses a
  writing run, then NEW. A live NEW marks each move of `LIVE_SESSION_KINDS` as
  `deferred while live`, skips those writes (`apply_home` skip set, no record rewrites, the
  resume tree re-keyed without the task merge) and runs the rest. On this host's record of the
  pair it appends a `deferred_recorded` bookkeeping row naming the deferred parts.
  `noted_deferral` reads that row back until a later migration or `finished` row.
- `src/ai_cli/session_migrate_resume.py`: the parts table reports a deferred part as
  `deferred: NEW is running; stop it with ai sessions stop NEW`, which never counts as done.
- `src/ai_cli/session_migrate_status.py`: `deferred_only` and `deferral_report`, the one
  wording both launch paths use.
- `src/ai_cli/main.py`: the launch finishes a pending pair even when attaching to a live NEW,
  and a pair left with deferred parts only never refuses the launch. The launch and the
  restart warn the first time and print a progress line after that. A writing
  `--finish` without `-y` that will defer asks for confirmation.
- `docs/tools/cc-session-adoption.md` and `CHANGELOG.md`.

## Verification

- Frozen tests in `tests/test_session_migrate_finish_while_live.py`, red on the base source:
  both writing finishes exited 2 (`is still running in the slot being finished`) where 1 was
  expected, and the attaching launch and the restart left the OLD-keyed launch log in place.
  The OLD-is-live case is a guard check and passes on both.
- Updated tests that pinned the old refusal: `tests/test_session_migrate_live_plan.py`,
  `tests/test_session_migrate_on_launch.py`, `tests/test_session_script_restart_finish.py`.
- The change has no platform branch.

## Lessons Learned

A guard that protects some files should gate the writes to those files, not the whole run. An
all-or-nothing refusal on a session that never stops is a permanent refusal, and repeating it
on every launch reports nothing new.
