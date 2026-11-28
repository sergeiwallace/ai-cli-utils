---
title: "A sync watcher started by a session keeps running after the session's home is deleted"
category: bug
tags: [bug, session-launch, sync, nats, process-lifecycle]
status: fixed
template_version: "bug-1.0.0"
---

# A sync watcher started by a session keeps running after the session's home is deleted

**Status:** fixed on the branch for this bug, not yet merged.

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix](#fix)
- [Verification](#verification)
- [Design Decisions](#design-decisions)
  - [D-1: What stops a watcher whose home is gone](#d-1)

## Summary

Every session started with `ai c` also starts `ai sync watch --auto` and `ai memory watch` in the
background. Both are meant to be one per machine, guarded by a PID file in the state directory, so
outliving one session is by design. A session started with a temporary `HOME` gets its own state
directory and so its own watchers. When that session ended and its `HOME` was deleted, both
watchers kept running, parented to the user's init process, until killed by hand. Fourteen of them
built up from one run of live session tests.

## Reproduction

1. Start a session on a private tmux server with `HOME` and the XDG directories pointed at a
   temporary directory, on a host where a NATS server answers on `localhost:4222`.
2. Let the agent exit so the session ends, then delete the temporary directory.
3. Expected: the watchers stop. Observed: `ai sync watch --auto` and `ai memory watch` are still
   running 15 seconds later, and indefinitely after that.

A torn-down server does not reproduce it: killing the tmux server while the pane is still open
hangs up the pane's process group, watchers included. The orphans came from sessions that ended on
their own.

## Root Cause

With a reachable NATS server, `sync_watch` connects and then awaits
`NATSClient.subscribe_durable`, which sleeps in a loop that only a cancel or Ctrl+C ends.
`memory_watch` loops on `time.sleep(0.5)` the same way. Neither loop looked at anything that could
tell it the home it served was gone, so deleting the home left both running for good.

`connect()` itself is bounded: with no server it gives up after three attempts and the watcher
exits. That is why the defect showed only on a host with a local NATS server.

## Fix

- `src/ai_cli/sync.py`: `_holds_pid_file(name)` reports whether the PID file still exists and
  names this process. `sync_watch` runs its subscription through `_run_while_pid_file_held`, which
  checks the file once a second and cancels the subscription when it is no longer held.
- `src/ai_cli/memory.py`: the memory watcher's loop runs only while it holds its PID file.
- `_release_pid_file` now removes the file only while it names this process, so a watcher that
  stops because another instance took the file over leaves that instance's file in place.
- `docs/tools/ai-cli-usage.md` states when each watcher stops.

`ai telemetry writer` uses the same pattern but no session starts it, so it is unchanged.

## Verification

- `tests/test_session_watcher_teardown.py::test_given_session_on_private_server_when_it_ends_and_its_home_is_removed_then_no_watcher_survives`
  runs the real generated session script on a private tmux server with the real watcher code;
  only `nats.connect` is faked, as a server that accepts every call. It failed before the fix with
  both watchers still alive after 15 seconds, and passes after it, unchanged.
- `tests/test_sync_watch.py::TestPidFileGuard::test_given_pid_file_taken_over_when_released_then_other_instance_keeps_it`
  covers the release change.
- 482 tests across the sync, memory, telemetry, messaging and CLI parsing files pass, and the
  session script, self-update and CLI files pass (33 and 203).

## Design Decisions

<a id="d-1"></a>

#### D-1: What stops a watcher whose home is gone — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** The watchers are machine-wide on purpose, so stopping them when one session ends would
break a real machine where several sessions share one watcher. What has to end is a watcher with
nothing left to serve.

##### (a) A watcher stops once its PID file is gone or names another process

**Pros:**
- Covers every way a home disappears, test or not, for both watchers.
- Keeps the one-per-machine design: ending a session does not stop the shared watcher.
- Deleting the PID file becomes a way to stop a watcher.

**Cons:**
- One small file read per second for each watcher.

##### (b) The launch path does not start the watcher where sync cannot apply

**Pros:**
- No running process at all on a machine without sync.

**Cons:**
- A machine that does sync, under a throwaway home, still leaves an orphan.
- Needs a new rule for when sync "applies", which the PID file does not.

##### (c) Test sessions do not start the watchers

**Pros:**
- Smallest change for the reported case.

**Cons:**
- Fixes the tests, not the product: any other temporary home still leaks.
- Leaves the session's watcher launch untested.

##### Recommendation

(a): it is the smallest change that makes the reproduced orphan impossible, it fixes both watchers
in one place, and it needs no new configuration.

> **Decision:** — (a) A watcher stops once its PID file is gone or names another process
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-54y8/D-1; decision-topic=watcher-orphan-stop-condition; governs=src:ai_cli/sync.py; normalized-proposition=a-watcher-stops-once-its-pid-file-is-gone-or-names-another-process; applicability=host:all; outcome-id=stop-on-lost-pid-file; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->
> **Post-build review:** behaviour-change: deleting a watcher's PID file now stops that watcher within about a second.
