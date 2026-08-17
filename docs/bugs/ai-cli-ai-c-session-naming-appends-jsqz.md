---
title: "ai c appends a second index to a custom name ending in a number"
category: bug
tags: [bug, session, tmux, worktree, naming]
status: fixed
severity: P1
task: AI-CLI-ai-c-session-naming-appends-jsqz
template_version: "bug-1.0.0"
---

# ai c appends a second index to a custom name ending in a number

**Status:** fixed

**Task:** `AI-CLI-ai-c-session-naming-appends-jsqz`

**Created:** 2026-08-17

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix](#fix)
- [Fix Log](#fix-log)

<!-- doc:region name="summary" kind="replaceable" -->

## Summary

`ai c` appended a second automatic index to a custom name that already ended
in a numeric segment. For example, `feature-1` was allocated as
`feature-1-1` instead of retaining the requested `feature-1` slot.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

From a registered project with prefix `myproject`, run:

```console
$ ai c feature-1
```

Before the fix, the launch allocated tmux session `c-myproject-feature-1-1` and
AI name `myproject-feature-1-1`. The requested explicit `-1` suffix should be
preserved, producing `c-myproject-feature-1` and `myproject-feature-1`.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause

`build_session_name()` recognized only a name composed entirely of digits as an
explicit slot. A custom name such as `feature-1` fell through to generic naming,
which always called `find_next_index()` and appended a second suffix.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix" kind="replaceable" -->

## Fix

Names whose final hyphen-separated segment is numeric now use the explicit-slot
path. That path checks for an existing local or remote tmux session (or bare
worktree) with the complete custom name and otherwise allocates that exact name.
Names without a trailing numeric segment continue to use automatic indexing.

This touches session-name allocation, which is also involved in the separately
tracked remote preview identity mismatch. It does not change remote preview
timing or attempt to resolve that separate issue.

<!-- /doc:region name="fix" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Commit | Notes |
| --- | --- | --- |
| 2026-08-17 | — | Added explicit trailing-index recognition and regression coverage for tmux, bare worktree, numeric-slot reuse, and ordinary auto-indexing behavior. |

<!-- /doc:region name="fix_log" -->
