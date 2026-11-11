---
title: "Migrated session resumes under its old name"
category: bug
tags: [bug, sessions, migration, claude-code]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Migrated session resumes under its old name

## Founding Ask Coverage

`N/A`: this fix was requested through a relayed summary, not a recorded founding ask. The
ask as relayed: after `ai migrate-session OLD NEW`, a resumed session must come back as NEW,
and `--finish` must find and repair a transcript still naming OLD.

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

A session migrated with `ai migrate-session OLD NEW` and relaunched with `--name NEW` still
reported OLD as its name. While it ran, tools that take the session name from Claude Code's
session record recreated state keyed by OLD.

A `--finish` run on the same session reported `transcript title | done earlier`, and the
post-migrate resolve check passed.

## Environment

Linux, Claude Code transcripts under `~/.claude/projects/<slug>/<uuid>.jsonl`. One measured
transcript held about 7,700 `custom-title` records naming NEW, which the migration had
rewritten, and about 7,700 `agent-name` records naming OLD, which it had not. The last record
of each type named OLD after the relaunch.

## Reproduction Steps

1. Create a transcript holding `{"type":"custom-title","customTitle":"OLD"}` and
   `{"type":"agent-name","agentName":"OLD"}` records.
2. Run `ai migrate-session OLD NEW -y`.
3. Observe that the moved transcript's `customTitle` records read NEW and its `agentName`
   records still read OLD.
4. Run `ai migrate-session OLD NEW --finish -y`. It reports the transcript part as done
   earlier and leaves the `agentName` records unchanged.

## Root Cause Analysis

Claude Code writes an `agent-name` record beside each `custom-title` record, and on resume
it restores the session's name from the last `agent-name` record. It then writes new
`custom-title` records under that name. Every place that renamed or inspected a transcript
read only `customTitle`:

- `retitle_transcript` in `src/ai_cli/session_adopt.py` rewrote only `customTitle`.
- `migrate_session` picked the transcripts to retitle with `transcript_titles`, which reads
  only `customTitle`.
- `_stale_transcript` in `src/ai_cli/session_migrate_resume.py`, the check a re-run and
  `--finish` use, matched only `customTitle == OLD`. Its rewrite also ran the retitle only
  while a `customTitle` naming OLD remained.
- `probe_resolves` checked only the current `customTitle`, so the post-migrate check passed
  on a transcript whose current `agentName` was OLD.
- `ClaudeCode.rename` in `src/ai_cli/session_clients.py`, the transcript-only rename a host
  sweep uses when the worktree is gone, matched only `customTitle`.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-11-11 | Relaunching the session with `--name NEW` after the migration | Claude Code restored OLD from the transcript's `agent-name` records |

## Fix

`RECORD_NAME_KEYS` in `src/ai_cli/cc_migrate.py` names both fields. Both retitlers rewrite
either field when it equals OLD. Transcript selection and `_stale_transcript` match
`agentName` too, and the resume rewrite retitles every stale transcript. `probe_resolves`
returns no match when the resolved transcript's current `agentName` names another session,
so the post-migrate check fails while Claude Code would restore that name. The probe still
runs only when a `customTitle` named OLD, because `ai c` resolves transcripts by
`customTitle`.

## Verification

- [x] `tests/test_session_migrate_agent_name.py` drives `ai migrate-session` and `--finish`
  through the CLI. It covers both record types and `agent-name` records alone, a `--finish`
  that reports and repairs a leftover OLD `agentName`, and a failing resolve check. All four
  tests fail before the fix and pass after it.
- [x] The worktree-gone sweep test in `tests/test_migrate_to_name.py` also asserts that the
  `agentName` record follows the new name.
- [ ] After the fix is installed, relaunch one migrated session and confirm that Claude Code's
  session record reports NEW. A fixture cannot run Claude Code, so this step needs a live
  session.

## Lessons Learned

A session's name is stored in more than one record type. A rename has to cover every field
Claude Code reads the name back from, and its verification probe has to read the same field
the client restores from.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
