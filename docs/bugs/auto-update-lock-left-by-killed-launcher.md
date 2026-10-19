---
title: "[AI-CLI-2kko] A launcher killed during auto-update leaves the update lock held"
category: bugs
tags: [bug, update, lock, launch, transport]
status: fix-in-review
severity: P1
task: AI-CLI-2kko
---

# [AI-CLI-2kko] Auto-update lock left by a killed launcher

**Status:** fix-in-review

**Severity:** P1 (every launch stalls 90 s, and the install never updates)

**Created:** 2026-10-19

**Task:** `AI-CLI-2kko`

## Symptoms

- Every `ai c` sat at `install: checking installed version` for 90 s and then
  continued on the old install. The next launch did the same.
- One remote session's `transport-outcomes/<session>.json` was 0 bytes.

Both were found while root-causing a remote launch that ended with `zsh: killed`
while the host was saturated. That run's SIGKILL came from outside the CLI: no code
path in this package sends SIGKILL to the launcher itself (see Rejected hypotheses).
The two defects below are what the kill left behind.

## Causal mechanism

1. `_auto_update_if_stale` serialized updates with a create-only lock file
   (`O_CREAT | O_EXCL`) and released it by deleting it in a `finally`.
2. A launcher killed while it held the lock never ran the `finally`, so the file
   stayed.
3. Every later launch with a stale install fingerprint failed the create, took the
   "a peer is updating" branch, and `_await_peer_update` polled for the file to
   disappear. It never did, so each launch waited the full 90 s bound.
4. The waiter never runs the update, so the fingerprint stayed stale and step 3
   repeated on every launch.

Separately, `_write_transport_outcome` used `Path.write_text`, which truncates the
file before writing. A client killed between the truncate and the write leaves an
empty record.

## Rejected hypotheses

- **The launcher signals its own process group.** `ProcfsProbe._signal_group`
  refuses to signal the caller's own group.
- **The restart path kills the launcher.** `session_restart` sends SIGKILL only to
  the recorded agent pid, after an identity check.
- **The tmux reaper or `ai ps clean` kills it.** Both send SIGTERM first, and
  `ai ps clean` runs only when a user confirms it.
- **The reinstall rewrites the running interpreter.** Driving the launch's
  reinstall path with a stale stamp completed and re-imported cleanly.

## Regression tests

RED on the unfixed code, GREEN after the fix:

- `test_given_lock_left_by_a_launcher_that_died_when_auto_update_runs_then_it_updates_without_waiting`
  (a real child process creates the lock and exits).
- `test_given_an_existing_outcome_record_when_it_is_rewritten_then_the_old_record_is_replaced_whole`
  (POSIX only; Windows refuses to replace a file another handle has open).

The negative constraint
`test_given_a_live_process_holds_the_lock_when_auto_update_runs_then_it_waits_and_does_not_update`
passes on both, so a live holder is still waited on.

## Fix

- The update lock is an exclusive OS lock (`portalocker`) on a persistent file.
  The OS releases it when the holder exits, killed or not. The file is never
  deleted, so a waiter and the next launcher always lock the same file.
- The outcome record is written to a temp file and renamed over the record, using
  the existing atomic JSON writer.

## Prevention

A lock whose release depends on the holder running cleanup code is a lock a kill
can strand. Use a lock the OS releases on exit, or record an owner a waiter can
check for liveness.
