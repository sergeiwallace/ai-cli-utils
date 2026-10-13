---
title: "The stale-session reaper was never started automatically"
category: bug
tags: [bug, session-launch, process-lifecycle, circus]
status: fixed
template_version: "bug-1.0.0"
---

# The stale-session reaper was never started automatically

## Summary

<!-- doc:region name="summary" kind="replaceable" -->

The independently supervised stale-session reaper existed, but only an explicit
`ai session-reaper start` registered it with Circus. A machine restart or Circus
failure therefore left the worker absent indefinitely, even while Claude sessions
were active.

<!-- /doc:region name="summary" -->

## Reproduction

<!-- doc:region name="reproduction" kind="replaceable" -->

At the unfixed revision, a behavior-level CLI test launched `ai c` through the
real Click entry point while isolating external process and session boundaries.
No reaper-start action occurred. Direct start tests also showed that an already
running watcher was removed and re-added, and two concurrent starts each added a
watcher rather than making one idempotent decision.

<!-- /doc:region name="reproduction" -->

## Root Cause

<!-- doc:region name="root_cause" kind="replaceable" -->

The launch path in `src/ai_cli/main.py` invoked update, tunnel, and session setup
hooks but no reaper lifecycle hook. The only registration path in
`src/ai_cli/process_manager.py` was the manually dispatched start command, and
that path unconditionally removed the watcher before adding it again.

Causal chain:

```text
`ai c` launch has no reaper lifecycle hook
  -> only a manually typed command can register the Circus watcher
  -> reboot or supervisor death removes the worker with no automatic retry
  -> no periodic stale-session evaluation runs while later sessions are active
```

The competing hypothesis that Circus alone would restore the worker was rejected:
Circus can respawn a registered watcher, but no daemon or watcher registration
survives a host reboot when the launch path never recreates it.

<!-- /doc:region name="root_cause" -->

## Scope of Fix

<!-- doc:region name="scope_of_fix" kind="replaceable" -->

The fix stays within one cohesive lifecycle boundary: the Claude launch hook and
the existing Circus registration helper. It adds no public API, dependency, or
reaper authority. The launcher schedules startup only; evaluation remains in the
independent worker. This is a contained, reversible fix rather than a redesign.

<!-- /doc:region name="scope_of_fix" -->

## Fix

<!-- doc:region name="fix" kind="replaceable" -->

Each non-dry-run local `ai c` launch now spawns `ai session-reaper start`
asynchronously and continues immediately. The registration command uses an
in-process lock plus a cross-process file lock, checks the named watcher first,
and leaves an active instance untouched. If Circus or the worker died, the same
path recreates and starts it. Failures emit one warning with the manual command
and never run the evaluator synchronously. Native Windows startup is a successful
no-op because native tmux is unavailable. The configured mode remains unchanged;
its default is still `observe`.

<!-- /doc:region name="fix" -->

## Verification

<!-- doc:region name="verification" kind="replaceable" -->

Frozen regressions cover CLI scheduling, non-blocking subprocess startup,
already-running idempotence, concurrent singleton registration, actionable
failure output, and native-Windows no-op behavior. The focused suite was confirmed
red before production changes and green afterward. Repository lint, formatting,
and full tests are the final gate.

<!-- /doc:region name="verification" -->

## Lessons Learned

<!-- doc:region name="lessons_learned" kind="replaceable" -->

A persistent worker is not self-healing unless a routinely exercised entry point
re-establishes both its supervisor and its registration. Singleton configuration
inside the worker is insufficient when the registration operation itself removes
and replaces the existing watcher.

<!-- /doc:region name="lessons_learned" -->

## Fix Log

<!-- doc:region name="fix_log" kind="append_only" -->

| Date | Result |
|---|---|
| 2026-10-12 | Reproduced missing launch hook and non-idempotent registration; froze failing regressions. |
| 2026-10-12 | Added asynchronous launch startup, serialized idempotent registration, portable no-op, and documentation. |

<!-- /doc:region name="fix_log" -->
