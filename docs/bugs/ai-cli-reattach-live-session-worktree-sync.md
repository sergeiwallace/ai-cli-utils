---
title: "Reattaching to a live session rewrites its worktree's git state"
category: bug
tags: [session, tmux, worktree, git]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Reattaching to a live session rewrites its worktree's git state

## Founding Ask Coverage

N/A: this public record covers one defect found while sweeping the remote-reattach
bug class (AI-CLI-fkvp). The caller tracks the rest of that sweep.
This coverage index is non-authoritative.

**Status:** resolved

**Severity:** P1

**Created:** 2026-10-15

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Table of Contents](#table-of-contents)
- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

`ai c N` (local or `-R`) for a session that is already running reuses its worktree and
then synchronizes it before attaching, under the agent working in it:

- An agent that had staged a large removal (more than 50 deletions) finds its index
  reset to `HEAD`: the "index corruption auto-heal" cannot tell a deliberate staged
  removal from corruption.
- `git pull --rebase --autostash` stashes the agent's uncommitted edits, rebases its
  branch and pops them back while the agent is reading and writing those files.
- When that pull fails, the launcher runs `git rebase --abort` and
  `git restore --staged .`, unstaging the agent's work. When it strands the tree, the
  launch refuses to attach to a perfectly healthy session.

## Environment

Any host. Reproduced on Linux with tmux 3.7c, a real git repository with an `origin`
remote, and a private tmux server.

## Reproduction Steps

1. Run a session `c-myproject-tst-2` whose worktree has 51 deletions staged.
2. Relaunch slot 2 with worktrees enabled.
3. Before the fix, the attach happens, but the worktree now has 0 staged deletions.

## Root Cause Analysis

`_do_session_launch` creates or reuses the worktree, registers it, then runs the sync
block unconditionally. The `tmux has-session` probe that establishes "this session is
already running, attach to it" comes later, at the attach-or-create decision. None of
the sync's safety nets ask whether a process is using the checkout. They only ask
whether git already showed a conflict before the pull.

Rejected: "move the has-session probe before the worktree setup and skip the whole
worktree block". The attach path still needs the worktree path for the session script
and the canonical registration, and reuse itself is idempotent configuration
(upstream, symlinks, trust, direnv approval), not a git mutation. Rejected: "has-session
alone". A session whose panes are all dead is replaced, not attached, so its checkout
should still be synchronized for the engine that is about to start.

## Prior Fix Attempts

| Date | What was tried | Outcome |
|------|----------------|---------|
| 2026-10-15 | First RED draft without an `origin` remote | Failed in setup (reuse requires a remote), not at the defect; corrected before freezing |

## Fix

Before the sync, decide whether this launch will attach to a running engine: worktree
in use, not bare, not `--sandbox` (which replaces the session), and
`tmux list-panes -s -t =<session> -F '#{pane_dead}'` reports a live pane. If so, report
"sync skipped" and leave git alone. Otherwise the sync runs as before. The `=` target is
exact: a bare name is a prefix match in tmux.

Residual, not fixed here: a bare-mode (`-b`) launch has no tmux session to ask, so a
second bare launch into a worktree with a live engine still syncs before the existing
live-process refusal. No reproduction was attempted for it.

## Verification

- Frozen regression
  `test_given_session_running_in_its_worktree_when_reattached_then_its_git_state_is_left_alone`
  in `tests/test_session_launch_integration.py`, real git with a bare origin, real
  tmux. `live`: RED on unfixed code (`assert 0 == 51`, "the reattach rewrote the
  running agent's index"). GREEN after: 51 staged deletions survive and `HEAD` is
  unchanged. `dead`: passes both before and after; the replaced session's worktree is
  still healed.
- `tests/test_session_launch_integration.py`: 26 passed.
- Full gate: ruff check exit 0, ruff format clean after formatting, pyright src/
  0 errors, pytest 4315 passed and 21 skipped.

## Lessons Learned

A mutation that runs on every launch must be guarded by what the launch is about to do,
and "attach to a running process" is a different launch from "start a process". Decide
that first, then let it gate every step that writes to state the running process owns.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->
<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->
<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

2026-10-15: Opus worker delegated for the remote-reattach bug class found this while
reading `_do_session_launch` end to end. Froze a two-case RED (live, dead), then gated
the sync on a live-pane probe. Scope signals negative: one condition in the launch
path, no public contract change.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
