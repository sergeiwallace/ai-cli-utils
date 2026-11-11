---
title: "Resume starts a fresh session after a migration left the transcript under its old title"
category: bug
tags: [bug, migrate-session, resume, session-supervisor]
status: fixed
template_version: "bug-1.0.0"
---

# Resume starts a fresh session after a migration left the transcript under its old title

**Status:** fixed

**Created:** 2026-11-11

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix Log](#fix-log)

## Summary

After `ai migrate-session OLD NEW` ran while the session was live, the session supervisor's next
relaunch started a fresh Claude Code session instead of resuming the old one, so the session lost
its whole conversation. From then on every relaunch resumed the fresh transcript, because it was
the only one titled NEW.

## Reproduction

`tests/test_resolve_continue_after_migration.py` builds the state a real host showed: one slot's
project directory holds

- the session's own transcript, retitled NEW by the migration and then titled OLD again by the
  running session,
- a fresh transcript titled NEW, started after the migration by the relaunch that missed,
- an unrelated transcript renamed to something else,

and the host's `session-migrations.jsonl` records the migration OLD -> NEW (plus its
context-announced and finished rows).

`ai internal resolve-continue-target <slot> NEW` printed the fresh transcript. With the fresh
transcript absent it printed nothing (exit 0), which the supervisor treats as "start fresh". The
bare launch (`ai c` without tmux) picked the fresh transcript too.

## Root Cause

Both resume paths in `src/ai_cli/main.py` choose a transcript only by its current title:
`_find_cc_session_candidates_by_title` keeps transcripts whose last `customTitle` is NEW. The one
fallback, `_find_lone_mismatched_cc_session`, applies only when the slot holds exactly one
transcript, and its confirmation answers no whenever stdin is not a terminal, which it never is
under the supervisor.

The migration retitles the transcript, but a session that is still running keeps its old name in
memory and appends a `customTitle` record with OLD on its next turn. Nothing in the resume path
read the migration ledger, so a transcript titled OLD was never considered for NEW.

## Fix Log

`_find_migrated_cc_session` runs in both resume paths before the title match is used. When the
ledger records a migration from OLD to NEW (the row that carries `worktree`), it returns the
newest non-background transcript currently titled OLD, or a numbered copy of OLD, provided every
transcript titled NEW was started (first timestamped record) after that migration. A transcript
titled NEW that started before the migration, or whose start cannot be read, keeps winning.

The fallback requires the OLD title to be the transcript's current one rather than anywhere in its
title history. A transcript renamed from OLD to some third name has become a different session,
and resuming it under NEW would attach the wrong conversation.

The fix does not rewrite transcripts. Making the retitle stick (so the transcript reads NEW and
the normal match finds it) is a separate change; this one keeps resume correct when the title has
already reverted.
