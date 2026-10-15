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

Follow-up: after the initial project-validation fix, the same reconnect reaches
worktree setup but exits with "could not create or reuse the isolated session worktree"
when the launcher starts outside the session's repository.

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
repository. Although a running pane owns its working directory, downstream launcher
worktree setup also needs the correct cwd. The initial fix overlooked that dependency:
`detect_repo_root()` runs `git rev-parse --git-common-dir` relative to the launcher,
and `create_worktree()` relies on that result to find the session's checkout.

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
skip remote project resolution and validation. Otherwise retain the original
strict refusal. Bare and unnamed launches continue through the original validation.
The later sandbox ownership, dead-pane handling, and attach logic remain intact.

The follow-up restores chdir only for the existing-session branch. Match the engine
and resolved tmux name in the canonical registry, then enter that worktree before
downstream repository discovery. The public registry reader returns only paths,
so this uses the existing schema-validated loader without changing its contract.
For unregistered sessions, including sessions launched without worktrees, use the
exact tmux target's pane directory. Ambiguous registry entries, malformed registries,
and inaccessible directories refuse instead of using the unrelated project or cwd.
The fresh-launch block is unchanged. The cause is launcher ordering, not a missing
Git worktree: the regression creates and registers a real checkout before launch.

## Verification

The regression in `tests/test_session_launch_integration.py` uses a real live session on
a private tmux server. Both numeric and fully qualified inputs must reach attach, preserve
the original session ID and avoid creation. The original test was frozen after its
two expected RED failures. A fresh tmux launch with an absent project must still exit 1
without creating a session; the existing bare remote and local refusal tests are unchanged.
The follow-up deliberately replaces the original "never chdir" assertion with chdir
to the session's own directory; its old invariant hid the worktree dependency.

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

Follow-up reproduction: `uv run pytest tests/test_session_launch_integration.py -k live_remote_worktree -n 0 -q --tb=short`
failed twice before production edits (exit 1), both with the reported worktree refusal.
The frozen cases enable real Git worktree reuse and run from a non-repository directory,
with the selected project either absent or unrelated. They require attach to the same
tmux identity and forbid worktree creation under either wrong directory.

Follow-up focused verification:
`uv run pytest tests/test_session_launch_integration.py tests/test_project_dir_required.py tests/test_chief_of_staff.py -q --tb=short`:
199 passed, exit 0.

Follow-up full gate:
`uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pyright src/ && uv run pytest`:
exit 0; lint passed, 218 files already formatted, zero type errors;
4302 passed, 19 skipped, 15 warnings in 59.80 seconds. Warning categories match the
earlier run above. Disabling only the new reattach block reproduced both failures
again (exit 1); restoring it and running
`uv run pytest tests/test_session_launch_integration.py -k 'live_remote or fresh_remote' -n 0 -q --tb=short`
passed all five cases (exit 0). Document validation and `git diff --check` also passed.

## Lessons Learned

Missing-directory tests covered fresh launches but did not combine an absent checkout
with an already-live remote session. Test prerequisites separately for session creation
and reattachment, and assert the actual attach handoff rather than accepting any exit.
Keep worktree creation enabled in reattach regression coverage: a live pane's cwd
does not establish the launcher's cwd.

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

2026-10-14: Follow-up implementation worker confirmed two worktree-enabled RED cases,
then restored the existing session's directory before worktree setup. Scope signals
were all negative: no shared abstraction, public contract, repository boundary, or
unrelated subsystem changed. The contained, reversible ordering change is sufficient;
no redesign is needed. This is an implement/medium task because it is a bounded
regression in an existing launch path. The fallback probe initially returned no
directory; a real private tmux probe established the complete exact pane target
`=SESSION:` with `pane_current_path`, and all 199 focused cases passed.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
