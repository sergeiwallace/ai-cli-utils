---
title: "A supervisor restart never finishes a pending migration to its session"
category: bug
tags: [bug, sessions, migration, supervisor, restart]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A supervisor restart never finishes a pending migration to its session

## Founding Ask Coverage

The ask: a supervisor restart of NEW, driven through the generated wrapper on fixture state
with a pending OLD -> NEW ledger row, runs the finish between agents; a refused finish still
restarts the agent; the live guard still refuses when an agent process is live. All three are
built and covered by the frozen test. Nothing in the ask was narrowed.

**Status:** resolved

**Severity:** P2

**Created:** 2026-11-15

## Table of Contents

- [Symptoms](#symptoms)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Decision](#decision)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

A session launched as NEW whose launch-time finish was refused (a hook refused, for example)
kept the migration OLD -> NEW pending for as long as it ran. Each time its agent exited, the
tmux supervisor started a replacement, and none of those restarts ran the finish. The ledger
gained no `finished` row and OLD-keyed files stayed in place until someone stopped the session
and ran `ai c NEW` or `ai migrate-session OLD NEW --finish`.

## Reproduction Steps

1. Migrate a slot OLD -> NEW and drop the `finished` row from the ledger, so the pair is pending.
2. Seed OLD-keyed launch logs, which only a finish moves.
3. Start the wrapper `ai c` generates for NEW. Let its first agent run for more than three
   seconds and exit non-zero, so the supervisor restarts it.
4. When the replacement agent starts, the OLD-keyed logs are still there and the ledger has
   no `finished` row.

`tests/test_session_script_restart_finish.py` does exactly this with fixture `tmux`, `claude`
and `ai` programs, running the real `ai_cli` for the run-state commands and the finish.

## Root Cause Analysis

1. The only call to `_finish_recorded_migrations` is in `_do_session_launch`
   (`src/ai_cli/main.py`), which runs for `ai c` and for a recipe replay by
   `ai sessions relaunch`.
2. The generated wrapper (`src/ai_cli/session_script.py`) is a persistent supervisor that
   starts one child body per agent. A child body starts the agent directly, and on a non-final
   exit it returns, after which the supervisor starts the next child. No step of that path calls
   `ai c` or `_do_session_launch`, so a restart never reaches the finish.
3. Adding a call alone would not have worked. `finish_session`
   (`src/ai_cli/session_migrate.py`) refuses whenever any tmux session hosts NEW, through
   `_refuse_live` and the check after it, and inside the wrapper NEW's own tmux session always
   hosts NEW.

Rejected hypothesis: that a supervisor still running under OLD needs the finish too. The
migration refuses while OLD's tmux session runs, so a supervisor under OLD exists only when
OLD was never stopped, and finishing OLD -> NEW under it would move files from under a running
OLD. `pending_finishes(OLD)` finds nothing there, and that is the intended answer.

## Decision

**Question:** how should a supervisor restart finish a pending migration, given that the
finish's live guard refuses NEW's own tmux session?

| Option | Pros | Cons |
| --- | --- | --- |
| A. The child body calls `ai internal finish-migrations` between agents; the finish leaves out NEW's own tmux session once tmux confirms the caller runs in it | Every other live check stands. The child body is reloaded on every restart after `ai update`, so a running session picks the fix up at its next restart | One more `ai` process per restart; it returns after one ledger read when nothing is pending |
| B. Make a restart go back through `ai c NEW` | Reuses the launch path unchanged | `ai c` from inside the pane attaches to or replaces its own tmux session, which breaks the stable pane leader the generation lease depends on |
| C. Keep relying on stop, migrate and relaunch | No code change | A session restarted only by its supervisor never finishes, which is the defect |
| D. Run the finish in the persistent supervisor loop | Runs once per restart, outside the child | The supervisor runs from the script it started with and is never reloaded, so running sessions would never get the fix |
| E. Leave out the named tmux session on the caller's word | Simplest | Any caller naming a session would skip the live guard |

**Recommendation:** A. It narrows the live guard by exactly one tmux session, only after tmux
reports that the caller's pane belongs to it, and only between agents, when the session's own
agent has exited. A live agent named OLD or NEW or running inside either slot, and any other
tmux session hosting either name, still refuse the finish. The finish writes every file through
a temporary file and an atomic link or replace, so a running bash keeps reading the script inode
it opened. It moves only OLD-keyed files, and a NEW-keyed file already present is left as a
conflict. A rewritten NEW script changes its mtime, and the next restart reloads it the way it
reloads any rewrite.

Decided 2026-11-15 by the fixing agent (Claude Opus 5.5).

## Fix

- `src/ai_cli/main.py`: `_finish_migrations_on_restart` runs `finish_session` for each pair
  `pending_finishes` returns for the session's name. Its `tmux_names` leaves out the caller's
  tmux session only when that session is a variant of the name and
  `tmux display-message -t $TMUX_PANE` reports it. A refusal, an exception or a part left is one
  warning naming the `--finish` command, and the call always exits 0. It is reached as
  `ai internal finish-migrations <tmux_session> <ai_name>`, with the records also written to
  the launch log.
- `src/ai_cli/session_script.py`: the child body runs that command on every restart
  (`first_run` false), after the hot-reload check and before the agent starts.
- `docs/tools/cc-session-adoption.md`: describes the restart finish beside the launch finish.

## Verification

- Frozen test, red on the unfixed code: the replacement agent found
  `['myrepo-3-20261012T192719.410821Z-4063408.log']` where it expected no OLD-keyed logs, and
  both live-guard cases found no finish line in the output.
- With the fix: all three cases pass. With only the wrapper call removed, all three fail again.
- `tests/test_session_migrate*.py` and `tests/test_session_script*.py`: 356 passed.
- The change has no platform branch. The test runs on Linux and macOS, and is skipped only on
  Windows, where the supervisor does not run.

## Lessons Learned

A guard written for "someone else is using this session" also catches the session asking about
itself. When a new caller must run inside the thing the guard watches, leave out exactly that
caller, proved by an outside party (here tmux), rather than weakening the guard for everyone.
