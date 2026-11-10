---
title: "Session migration recovery and truthful completion"
category: bug
tags: [bug, sessions, migration, recovery]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Session migration recovery and truthful completion

## Founding Ask Coverage

Repeat the original migration command after complete, partial or interrupted work;
check durable artifacts, finish remaining steps and report each part. Preserve
existing data and refuse ambiguous destinations.

## Symptoms

A named dry run printed only a proposed name, even after successful migration.
A named migration returned success after a hook failed. Repeating the original
command after the worktree moved refused because OLD was no longer registered.

## Environment

Reproduced on the local baseline containing the virtualenv relocation change.
Tests drive the command with temporary repositories, a fake home and a local
bare name registry. No live migration is part of verification.

## Reproduction Steps

Run the command once, then repeat it. Alternatively interrupt it after the
worktree move, or declare a hook that plans successfully and fails when applying.
For dry-run parity, request a spare, an unknown name or a filtered word.

## Root Cause Analysis

The named preview returned before name validation and migration planning. The
named applying path published completion without inspecting the migration result.
The core starter required OLD to be registered; its separate finishing path did
not repair the home branch, canonical registry, project directory, transcript
rewrites or virtualenv. Moving a worktree alone therefore could not resume.

## Prior Fix Attempts

Virtualenv relocation already repaired scripts during the first migration. It
did not make an interrupted invocation of the original command discover recovery.
Calling an individual failed hook also left the pipeline's completion untracked.

## Fix

The original command dispatches to recovery when current worktrees and durable
pair evidence prove a move. Recovery plans missing home steps and reuses the
existing old-name artifact planners. Named migrations inspect failed parts before
publishing naming completion. Previews run the same available name and hook
checks. Tables and exit codes distinguish completion, partial work and refusal.

A new authoritative completion journal was rejected: a stamp can outlive a failed
write or omit a newly added migration step. The existing append-only intent ledger
and current artifacts establish recovery; ambiguous state is refused. Hook-owned
status remains the hook's responsibility. No production dependency was added.

## Verification

The command regressions failed on the baseline: `assert 1 == 0` on rerun,
`assert 0 == 1` on named hook failure, and `assert 0 == 2` on invalid-name
previews. The corrected wrapper assertion checks behavior rather than counting
occurrences in generated text. Focused tests cover 13 interruption boundaries,
repeat success, collisions, liveness, missing evidence and hook retry.

## Lessons Learned

A naming record records allocation, not completion of every migration part.
Recovery must inspect artifacts after a directory move and must propagate failed
hook results to callers. A read-only plan cannot promise that a time-sensitive
snapshot stays fresh until the hook applies it.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-11-10 | Added command-level RED regressions and artifact-based recovery | Focused recovery tests pass |

<!-- /doc:region name="summary" -->
<!-- doc:region name="reproduction" kind="replaceable" -->
<!-- /doc:region name="reproduction" -->
<!-- doc:region name="root_cause" kind="replaceable" -->
<!-- /doc:region name="root_cause" -->
<!-- doc:region name="fix_log" kind="append_only" -->
<!-- /doc:region name="fix_log" -->
<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->

## Interrupted part follow-up

Command regressions also reproduced a copied task being allocated a second ID
(`assert 4 == 3`), stale memory index links after their file rename, matching
metadata copies being mistaken for a collision (`assert 1 == 0`), and a flat
owned task worktree staying at its old path (`assert (False)`). An agent stopped
between its move and branch rename retained its old owner; a chief stopped between
registration and registry writes retained its old registry name.

Task recovery now saves the ID mapping and before/after hashes before copying.
The append-only task journal is intentionally limited to the non-derivable ID
mapping; it cannot override artifact checks. Without it, ambiguous task copies
are refused. Memory indexes and chief fields are detected independently from the
files already moved. Agent recovery detects remaining branch/ownership updates
at the new path, and flat paths require branch ownership evidence.
Two further RED regressions found that a topic-branch checkout moved before its
receipt had no usable pair evidence (`assert 2 == 0`), and explicit finishing
omitted the parts table. Intent is now flushed before the move, every command
result gets a table, and informational hook output cannot mask `done earlier`.
