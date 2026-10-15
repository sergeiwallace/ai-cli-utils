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

Second follow-up (AI-CLI-fkvp): with both fixes merged, `ai c 1 -R`, with or without
`-p`, against a session whose pane had died exited with
`cannot enter existing session directory: cannot resolve directory for c-r-<ai_name>`.
The `-p`-less reproduction rules project selection out: the refusal is inside the
existing-session branch, which never consults it.

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

Second follow-up root cause: the canonical worktree registry is keyed by **path**, and
every registration overwrites that entry's `engine` and `session_name`. The launcher
registers `(c, c-r-<ai_name>)`; seconds later a Claude Code SessionStart hook registers
the same path as `(claude-session-start, <ai_name>)`. Last writer wins, so the hook's
shape is what a real host holds (13 of 15 entries on the reporting host; the other two
belonged to sessions where the hook had not re-registered). The first follow-up's lookup
required `session_name == c-r-<ai_name>` **and** `engine == c`, so it almost always
missed and fell through to tmux `#{pane_current_path}`. A dead pane (its `remain-on-exit`
session survives a crashed or exited engine) reports that format as empty, so the
fallback refused. The registry had the answer the whole time, under the other key.

Rejected: "the engine should scope the match". `ai_name` carries no engine segment, so
every engine's session in one slot shares the `.worktrees/<ai_name>` checkout, and the
prefixed tmux name already encodes its engine. Rejected: "fall through to the
fresh-launch project path for a dead pane". That re-couples reattach to `-p`, the
dependency the original fix removed.

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

Second follow-up fix: match a registry entry whose `session_name` is either the
prefixed tmux name or the bare `ai_name` (case-insensitively, as `_matching_worktrees`
already compares slot directories), whatever engine wrote it. More than one distinct
path still refuses as ambiguous. With no entry, the tmux fallback now asks for
`#{?pane_current_path,#{pane_current_path},#{pane_start_path}}`: a dead pane keeps the
directory it started in, which is the directory its launch stood in. The later
all-dead-pane handling then replaces the session in that checkout.

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

Second follow-up verification (AI-CLI-fkvp):

- Frozen regression
  `test_given_dead_remote_session_when_relaunched_from_elsewhere_then_reuses_its_worktree`,
  parametrized `session-start-hook` (hook-shaped entry, pane started somewhere unrelated,
  so only the registry can answer) and `nobody` (no entry, pane started in the repo, so
  only `pane_start_path` can answer). Both use a real git worktree and a real dead pane
  on a private tmux server. RED on unfixed code: 2 failed, both with
  `cannot resolve directory for c-r-myproject-tst-2`. GREEN after the fix.
- A first draft also compared session ids before and after, which cannot work: killing
  a server's only session ends that server and the next numbers its session `$0` again.
  That assertion was dropped and RED re-proved on reverted production code before
  re-applying the fix.
- `uv run pytest tests/test_session_launch_integration.py`: 24 passed.
- Full gate: ruff check exit 0, ruff format --check exit 0, pyright src/ 0 errors,
  pytest 4313 passed and 21 skipped once a docstring the public-name hygiene scan
  flagged was made generic.
- Live replay on the reporting host, against the real dead-pane session, of the remote
  half of the launch (`ai c --is-remote --project-prefix <P> [--project <repo>] --dry-run
  c-r-<ai_name>`, run from `$HOME`; the dry-run exit comes after this resolution and
  before any write). The installed unfixed build printed the reported error both with
  and without `--project`. The fixed build resolved the session's real
  `.worktrees/<ai_name>` checkout and its branch both times and exited 0.

## Lessons Learned

Missing-directory tests covered fresh launches but did not combine an absent checkout
with an already-live remote session. Test prerequisites separately for session creation
and reattachment, and assert the actual attach handoff rather than accepting any exit.
Keep worktree creation enabled in reattach regression coverage: a live pane's cwd
does not establish the launcher's cwd.

A reader of shared state must accept every shape its writers produce. Before matching
on a registry field, find every writer of that file (here, two repositories) and test
the shape the dominant writer leaves behind, not the shape this code path wrote.

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

2026-10-15: Second follow-up (AI-CLI-fkvp), Opus worker delegated by the session
tracking the reattach chain. Read the live registry (hook shape dominant) and the dead
pane's formats on a private tmux server (`pane_current_path` empty, `pane_start_path`
kept), froze the two-case RED, then broadened the registry match and added the
start-path fallback. Scope signals all negative: one block in the existing-session
branch, no schema or public contract change.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->
<!-- /doc:region name="appendix_evidence" -->
