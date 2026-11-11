---
title: "Migration name previews bypass refusal checks"
category: bug
tags: [bug, session, naming]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Migration name previews bypass refusal checks

## Symptoms

`ai migrate-session OLD --to-name NAME --dry-run` reported success for a held
spare, an unknown name, a common word, an occupied destination and a running session.

## Root Cause Analysis

The preview in `main._migrate_one_to_name` composed a title without calling
`name_registry.check_hand_chosen` or the filesystem and liveness preflight in
`session_migrate.migrate_session`. The real run called both.

## Fix

The preview now checks a supplied name against the existing registry validator,
or picks with the same static filters, then runs the real migration preflight
with `dry_run=True`. It reserves nothing. Reusing the validators was chosen over
a second set of preview-only checks, which would drift again.

## Verification

The parametrized entry-point regression
`test_given_a_refused_target_when_migration_dry_runs_then_real_refusal_is_reported`
failed in all five cases before the patch (`5 failed in 1.65s`). The migration-name
suite passed after it (`28 passed in 3.35s`). Each case asserts that the registry
tip and source slot remain unchanged.

The existing refreshed-host entry-point test used a repository with no source
slot. Re-running it at the preview-fix commit reproduced
`is not a registered worktree` because previews now run the real preflight.
Its fixture now supplies the migrate suite's actual registered source slot;
this preserves the host-refresh assertion while satisfying that preflight.

## Lessons Learned

A preview must exercise the same refusal boundaries as execution. Printing a
candidate title alone cannot establish that a migration is possible.

<!-- /doc:region name="summary" -->
