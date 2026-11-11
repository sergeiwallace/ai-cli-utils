---
title: "Migrated session renamed back to its old name by its Claude Code job record"
category: bug
tags: [bug, sessions, migration, claude-code]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Migrated session renamed back to its old name by its Claude Code job record

## Founding Ask Coverage

`N/A`: this fix was requested through a relayed summary, not a recorded founding ask. The
ask as relayed: after `ai migrate-session OLD NEW`, a session relaunched with
`ai c NEW -r` must keep the name NEW, and the next relaunch must still resume it.

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-11

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

A session migrated with `ai migrate-session OLD NEW` was relaunched with `ai c NEW -r`. The
relaunch resumed the right transcript, with `claude --resume <id> --name NEW`, and the
session still came back named OLD:

- the transcript gained a `custom-title` record and an `agent-name` record naming OLD,
  written at startup before the first turn, after every earlier name record had read NEW;
- Claude Code's live session record, `~/.claude/sessions/<pid>.json`, had `"name": OLD`;
- the prompt divider showed OLD.

Tools that take a session's name from that record then acted as OLD. One inbox tool
refused every message addressed to NEW as "not addressed to OLD".

## Environment

Linux, Claude Code 2.1.322. The session had been run in the background once, weeks before
the migration, so Claude Code had a job record for it:
`~/.claude/jobs/<first 8 characters of the session id>/state.json`, holding `"name": OLD`,
`"nameSource": "user"`, `"backend": "daemon"` and `"sessionId"` equal to the transcript's.
The launch wrapper and the process environment named only NEW.

## Reproduction Steps

1. Have a session whose id has a job record under `~/.claude/jobs/<short id>/state.json`
   naming OLD with `nameSource` `user`.
2. Run `ai migrate-session OLD NEW -y`. Every name record in the moved transcript reads NEW;
   the job record still names OLD.
3. Relaunch with `ai c NEW -r`. Claude Code appends a `custom-title` and an `agent-name`
   record naming OLD, and its session record names OLD.

`tests/test_session_migrate_job_name.py` drives the `ai` side of this on a fixture home.

## Root Cause Analysis

Claude Code renames a resumed session to the name in its job record. In the 2.1.322 bundle
(`~/.local/share/claude/versions/2.1.322`, read as bytes):

- The job id of a session with no `CLAUDE_JOB_DIR` is its session id's first 8 characters:
  `function vy(){let e=i.CLAUDE_JOB_DIR;if(e)return K(e);let n=Sb();if(n)return K(n.jobDir);return G().slice(0,8)}`,
  and the job directory is `function No(e){return U(hU(),e)}` under the jobs root.
- The `[jobStateNameSync]` watcher reads that directory's `state.json` at startup and on
  every change. When the record's `name` has `nameSource` `user` and differs from the
  session's current name (`if(rt===nue()||rt===wy(h.id))return;`), it calls
  `Gqe(rt, "peer", ...)`, the rename routine. That routine appends a `custom-title` record,
  then an `agent-name` record (`saveAgentName`), then writes the name into the session
  record with `O3(...)`. The `--name` value the launch passed is the current name it
  replaces.

This matches the measured transcript exactly: one `custom-title` and one `agent-name`
record naming OLD, written together at startup, after records that all read NEW. Claude
Code then re-appends its in-memory name after every turn, so every later pair also names
OLD.

`ai migrate-session` and `--finish` renamed every transcript record naming OLD, but never
read or wrote the job record, so the record kept naming OLD. Nothing about the launch
could override it.

An earlier fix stated that Claude Code restores the name from the transcript's last
`agent-name` record. That is not what happened here: every `agent-name` record read NEW
before this resume, and the name still reverted.

What the resume picked was already right. With the transcript's current title now OLD and
no transcript titled NEW, `ai internal resolve-continue-target <slot> NEW` falls back to
the transcript a recorded migration left under its old title (`_find_migrated_cc_session`)
and prints the migrated transcript. The tests pin that.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11-11 | Renaming `agent-name` records as well as `custom-title` records | The transcript read NEW throughout; the resume still wrote OLD, taken from the job record |

## Fix

`plan_job_names` and `apply_job_name` in `src/ai_cli/session_migrate.py` handle the job
record of each transcript in the session's project directory. A record is renamed only
when its `sessionId` or `resumeSessionId` is that transcript's id, since two sessions can
share the short id, and only when its `name` is OLD or a numbered copy `OLD (N)`, which
becomes `NEW (N)`. The rewrite keeps Claude Code's layout (indent 2), publishes atomically
and refuses when the file changed since it was read.

- `ai migrate-session OLD NEW` plans the rename as `job name` and applies it after the
  transcripts are retitled.
- `--finish` plans it in `plan_home`, so a pair already migrated, or already finished,
  gets it on a re-run: `job name | would do` with `-n`, `done now` with `-y`, `done earlier`
  after. The same finish also rewrites the OLD name records the reverted resume appended.

A job record naming another session is left alone, and a session that was never migrated
is not touched.

A session already reverted, still running under OLD, is not repaired by this change alone.
It needs to be stopped, finished with `ai migrate-session OLD NEW --finish -y` (the
launch-time finish runs only for pairs with no finished row), and relaunched with
`ai c NEW -r`.

## Verification

- [x] `tests/test_session_migrate_job_name.py`, through the CLI on a fixture home:
  - a migration renames the moved session's job record to NEW, and the resolver prints
    the moved transcript;
  - for a pair whose job record still names OLD and whose resume appended OLD name records
    after the migration, the resolver and `_find_migrated_cc_session` still pick the
    migrated transcript; `--finish -n` reports the job record, `--finish -y` renames it and
    leaves no OLD name record, and a second finish reports it done earlier;
  - another session's job record under the same short id is left alone;
  - a session that was never migrated resolves as before and its job record is untouched.

  At the base commit the first three fail on the job-name part and the last passes; all
  four pass with the fix.
- [ ] After the fix is installed, relaunch one migrated session that has a job record and
  confirm that its session record and new name records read NEW. A fixture cannot run
  Claude Code, so this step needs a live session.

## Lessons Learned

Claude Code keeps a session's name in more places than the transcript: here, a job record
under a short id that the transcript never mentions. When a rename does not stick, look
for every store keyed by the session id, not only the ones keyed by its name, and check the
client's own code for which one it restores from.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
