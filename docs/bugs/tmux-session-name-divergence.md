---
title: "Session custom title and tmux name divergence investigation"
category: bug
tags: [bug, session, tmux, naming]
status: fixed
severity: P2
template_version: "bug-1.0.0"
---

# Session custom title and tmux name divergence investigation

**Status:** fixed

**Created:** 2026-08-18

<!-- doc:region name="summary" kind="replaceable" -->

## Summary

The tmux native status bar could show a stale default window name even while
the session name and custom title were correct. This affected terminals that
render tmux's own status line.

## Reproduction

The tmux session name was correct. The apparent `0` was tmux's default
zero-based window index, not the session-slot index. The stale text was the
tmux window name.

## Root Cause Analysis

The launcher disables tmux `automatic-rename` for every session so iTerm2
title handling remains stable. Without an explicit `tmux rename-window`, the
window therefore remained named `tmux` in non-iTerm2 terminals, even though
the session's `ai_name` was available at launch.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
| --- | --- | --- | --- |
| 1 | 2026-08-18 | Isolated mocked-tmux reproduction and source/history trace | Session naming was correct; window naming was not yet isolated. |

## Fix

`_rename_tmux_window()` now invokes `tmux rename-window -t <session_id>
<ai_name>` after tmux configuration for both new sessions and re-attaches.
This preserves the existing `automatic-rename off` behavior while ensuring
tmux's native status line displays the session identity on every terminal.

## Verification

- Focused regression tests mock `subprocess.run`, assert the exact tmux rename
  command, and cover both launch paths.
- Repository-wide lint and formatting may report unrelated existing paths;
  verify the current branch's full gate alongside this change.

## Lessons Learned

Distinguish tmux session names from tmux window names and indices. Disabling
automatic window naming requires an explicit replacement name wherever the
native tmux status line is expected to reflect application identity.

## Fix Log

| Date | Commit | Notes |
| --- | --- | --- |
| 2026-08-18 | — | Added explicit tmux window naming after session creation and re-attach. |

<!-- /doc:region name="summary" -->
