---
title: "Resume picks a stray transcript titled NEW over the one the migration moved"
category: bug
tags: [bug, migrate-session, resume, session-supervisor]
status: fixed
template_version: "bug-1.0.0"
---

# Resume picks a stray transcript titled NEW over the one the migration moved

**Status:** fixed

**Created:** 2026-11-11

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix Log](#fix-log)

## Summary

A session relaunched under NEW before `ai migrate-session OLD NEW` finished starts a short
transcript titled NEW from its first record. Once the migration retitles the session's own
transcript to NEW, two transcripts carry NEW. The resume took the most recently active of them,
which was the stray, so `ai c NEW` and the session supervisor resumed a near-empty conversation
and the migrated one was lost.

On a real host, `ai internal resolve-continue-target <slot> NEW` reported two transcripts titled
NEW: a stray of about 1 MB, last active five minutes after a migrated transcript of about 470 MB,
and it resumed the stray.

## Reproduction

`tests/test_resolve_continue_after_migration_stray.py` runs the real migration on a fixture home
and then writes a transcript titled NEW into the moved project directory:

- With the stray more recently active, `ai internal resolve-continue-target <slot> NEW` printed
  the stray. With the migrated transcript more recently active it printed the right one, but its
  note did not say a transcript was skipped.
- For a pair whose ledger names no transcript and already has a `finished` row, a re-run
  `ai migrate-session OLD NEW --finish -y` recorded nothing, so the resume kept the stray. This
  held both when the migrated transcript still carried OLD and when it was already fully
  retitled to NEW, as on the real host, where the re-run's `transcript title` part read
  `done earlier`.

## Root Cause

`_find_cc_session_candidates_by_title` in `src/ai_cli/main.py` sorts every transcript currently
titled NEW by mtime, newest first, and both resume paths took `candidates[0]`. The only migration
fallback, `_find_migrated_cc_session`, covers a transcript that reverted to OLD; it returns `None`
whenever the migrated transcript is itself titled NEW. Nothing told the resume which transcript
the migration moved: the ledger rows carried `old`, `new`, `repo` and `worktree` but no transcript
id, although the migration computes that list (`named` in `_start_session`) and `--finish`
computes its own (the `transcript title` part of `plan_home`).

## Fix Log

- The migration's ledger row now lists the session ids of the transcripts it moves
  (`"transcripts"`).
- `--finish`, on the same ledger record it needs before it re-keys Beads issues, appends a
  `"transcripts_recorded": true` row naming each transcript it retitles that no row names yet. It
  does so on a finished pair too, so a pair migrated before the ledger named transcripts gains the
  record on its next finish. `pending_finishes` and `migration_recorded` ignore that row, so it
  never makes a pair pending again.
- Both resume paths now pick through `_pick_cc_resume_transcript`: the newest transcript titled
  NEW that the ledger lists as moved, then the reverted-title fallback, then the most recently
  active transcript titled NEW. When several are titled NEW, the note names the transcript it
  resumes and each one it skipped because no migration moved it.

- A finish also records each transcript in NEW's project directory whose first record predates
  the pair's first migration row (the row that carries `worktree`). The migration refuses while
  NEW's project directory exists, so such a transcript came from OLD's, while a stray started by a
  relaunch under NEW begins after the row. This is what reaches a pair already finished and fully
  retitled, where the `transcript title` part has nothing left to do (`done earlier`) and an old
  title or path no longer marks the moved transcript. A row written by hand carries no `worktree`
  and is not used, and a transcript whose start cannot be read is not counted.

No transcript is renamed, retitled, moved or deleted by this change; a stray stays readable. A
slot with no migration on record resumes exactly as before.

One case stays open: when a pair has no ledger row of its own, the recovery path of
`ai migrate-session` writes one when it resumes, after the worktree moved. A stray started between
the move and that recovery would begin before the row and be recorded as moved.
