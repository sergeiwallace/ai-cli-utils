---
title: "A session with auto-restart on ends instead of restarting after the agent exits cleanly"
category: bug
tags: [bug, session-launch, tmux, auto-restart, process-lifecycle]
status: fixed
template_version: "bug-1.0.0"
---

# A session with auto-restart on ends instead of restarting after the agent exits cleanly

**Status:** fixed in [PR 637](https://github.com/sergeiwallace/ai-cli-utils/pull/637), merged as
`bec4aa4a`.

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix](#fix)
- [Verification](#verification)
- [Design Decisions](#design-decisions)
  - [D-1: How a clean exit after a healthy run is treated](#d-1)
  - [D-2: Default length of the stop window](#d-2)

## Summary

With `[session] auto_restart` on, a tmux session should bring its coding agent back after the agent
exits. It did not when the agent exited with status 0, for example after `/exit`: the session
wrapper ended the session instead.

## Reproduction

1. Start a session with `ai c` on a private tmux server, with auto-restart on.
2. Let the agent run for more than a few seconds, then type `/exit`.
3. Expected: the agent starts again in the same pane. Observed: the pane closes and the session ends.

## Root Cause

Two earlier changes made the session wrapper read every clean exit (status 0) as a deliberate stop,
so it never reached the restart branch for that status. A crash (non-zero status) still restarted,
which is why the feature looked partly alive.

## Fix

The wrapper in `src/ai_cli/session_script.py` now restarts after a clean exit from a healthy run,
behind a short stop window:

- After a healthy run exits cleanly, it waits `[session] restart_stop_window_seconds` (default 3)
  and then restarts.
- One Ctrl+C inside that window stops the session for good.
- `0` restarts at once with no window. A fraction rounds up, and an invalid value falls back to 3
  (`src/ai_cli/config.py`, read at each clean exit).
- A clean exit within the first 3 seconds after start still stops, so a broken launch cannot loop.

`docs/procedures/exiting-a-cc-session.md`, `docs/tools/ai-cli-usage.md` and
`docs/reference/config.toml` describe how to stop a session.

## Verification

- A regression test failed before the fix and passes after it (`tests/test_restart_run_state.py`,
  `tests/test_session_interrupt_shell.py`).
- On a private tmux server: `/exit` restarted, Ctrl+C in the window stopped, and an early clean exit
  stopped.
- After the last main merge, 643 restart, session-script, config and tmux tests passed locally, and
  all 21 CI checks passed, Windows and macOS included.

## Design Decisions

<a id="d-1"></a>

#### D-1: How a clean exit after a healthy run is treated — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** Auto-restart has to bring the agent back after `/exit`, yet the person at the pane
still needs a way to stop the session. A clean exit alone cannot tell the two apart.

##### (a) Restart after a short window in which Ctrl+C stops

**Pros:**
- Restores auto-restart for the common `/exit` case.
- Keeps a one-key way to stop.

**Cons:**
- Stopping now needs a key press inside the window.

##### (b) Never restart after a clean exit

**Pros:**
- No change to how a session is stopped.

**Cons:**
- Leaves the reported bug in place for `/exit`.

##### Recommendation

(a): it fixes the reported failure and keeps stopping to a single key. The early-exit guard keeps a
broken launch from looping.

> **Decision:** — (a) Restart after a short window in which Ctrl+C stops
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-nz40/D-1; decision-topic=clean-exit-restart; governs=src:ai_cli/session_script.py; normalized-proposition=a-clean-exit-after-a-healthy-run-restarts-after-a-stop-window; applicability=host:all; outcome-id=restart-with-stop-window; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->
> **Post-build review:** behaviour-change: stopping a session now takes one Ctrl+C inside the window after the agent exits, where before a clean exit stopped it.

<a id="d-2"></a>

#### D-2: Default length of the stop window — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** The window has to be long enough to react to and short enough that a restart does not
feel slow.

##### (a) 3 seconds, configurable

**Pros:**
- Long enough to press Ctrl+C after seeing the exit.
- Matches the existing 3-second early-exit guard.

**Cons:**
- Adds 3 seconds to every clean-exit restart.

##### (b) No window by default

**Pros:**
- Fastest restart.

**Cons:**
- No way to stop a session from the pane without changing config.

##### Recommendation

(a): `restart_stop_window_seconds = 0` gives (b) to anyone who wants it.

> **Decision:** — (a) 3 seconds, configurable
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-nz40/D-2; decision-topic=restart-stop-window-default; governs=config:session.restart_stop_window_seconds; normalized-proposition=the-stop-window-defaults-to-3-seconds; applicability=host:all; outcome-id=three-seconds; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->
