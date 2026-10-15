---
title: "Remote reattach incorrectly requires a project directory"
category: bug
tags: [session, remote, tmux]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Remote reattach incorrectly requires a project directory

## Founding Ask Coverage

N/A: this public record covers the delegated remote reattach ordering defect only.
The operator's session migration and command comparison requests remain with the caller.
This coverage index is non-authoritative.

**Status:** resolved

**Severity:** P1

**Created:** 2026-10-14

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

A remote reconnect exits with "the project directory for this session does not exist"
even though its tmux session is still running. An unrelated or outdated project selection
prevents reaching the live pane.

## Environment

Reproduced on Linux with Python 3.14 and tmux, using a test-owned private server.
The affected path is the remote host's `--is-remote` launch invocation.

## Reproduction Steps

1. Keep `c-r-myproject-tst-2` running in tmux.
2. Invoke the remote-side launcher for slot `2` or its full session name, with the
   `myproject` prefix and `-p otherproject` resolving to an absent directory.
3. Before the fix, the launcher exits before invoking `tmux attach-session`.

## Root Cause Analysis

In `_do_session_launch`, the remote project resolution, directory validation, and chdir
ran before session-name construction and the existing-session probe. The missing-directory
refusal is necessary for fresh launches: otherwise a worktree can be created from the wrong
repository. A running pane already has its own working directory, so reattachment does not
need the launcher's chdir.

Alias resolution was not the defect: a valid alias can select an absent repository while
the requested session is already running elsewhere. No naming-contract or migration change
is needed; the existing session-name builder resolves both numeric and full-name inputs.
The fix fits entirely inside the remote project branch without changing public APIs.

## Prior Fix Attempts

| Date | What was tried | Outcome |
|------|----------------|---------|
| 2026-10-14 | Run the new live-session regression before editing production code | Both input forms failed at the missing-directory refusal, before exec |
| 2026-10-14 | Add an early `has-session` subprocess probe | Focused tests passed; four full-suite dry-run tests rejected the new subprocess boundary |

## Fix

Inside the remote branch, resolve a named, non-bare launch using the existing naming
helpers and check exact membership in the existing tmux session-list helper before project resolution. If the target exists,
skip remote project resolution, validation, and chdir. Otherwise retain the original
strict refusal. Bare and unnamed launches continue through the original validation.
The later sandbox ownership, dead-pane handling, and attach logic remain intact.

## Verification

The regression in `tests/test_session_launch_integration.py` uses a real live session on
a private tmux server. Both numeric and fully qualified inputs must reach attach, preserve
the original session ID, avoid creation, and never chdir. The test was frozen after its
two expected RED failures. A fresh tmux launch with an absent project must still exit 1
without creating a session; the existing bare remote and local refusal tests are unchanged.

- `uv sync --locked --dev`: exit 0.
- `uv run pytest tests/test_session_launch_integration.py -k live_remote_session -n 0 -q --tb=short`:
  RED before production edits, 2 failed, exit 1; both failed at the missing-directory refusal.
- `uv run pytest tests/test_session_launch_integration.py tests/test_project_dir_required.py tests/test_chief_of_staff.py -q --tb=short`:
  GREEN, 197 passed, exit 0.
- Full gate: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pyright src/ && uv run pytest`.
  Exit 0: lint passed, 218 files formatted, no type errors; 4300 passed, 19 skipped,
  15 warnings in 53.86 seconds. Warnings concern sync coroutines, dependency deprecation,
  and threaded process-fork tests outside the changed launch path.
- Document validation and `git diff --check`: exit 0.

Live operator reconnection and macOS/Windows execution are outside this local Linux verification.

## Lessons Learned

Missing-directory tests covered fresh launches but did not combine an absent checkout
with an already-live remote session. Test prerequisites separately for session creation
and reattachment, and assert the actual attach handoff rather than accepting any exit.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->
<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->
<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

2026-10-14: Implementation worker applied a remote-branch-only ordering fix after
confirming RED against a real tmux session. No prior production patch attempts.

2026-10-14: Replaced the initial extra `has-session` subprocess with exact membership
in the existing session-list helper after the full gate exposed four dry-run test
boundary failures. The frozen regression was unchanged; 197 focused tests then passed.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
