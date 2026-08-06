---
title: "[BUG-009] Remote Control startup override was still gated by an obsolete marker"
category: bugs
tags: [session, remote-control, growthbook, launcher, regression]
status: fix-deployed
severity: P1
task: AI-CLI-165
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-009] Remote Control startup override was still gated by an obsolete marker

**Status:** fix-deployed

**Severity:** P1 — previously paired Claude Code sessions did not receive the launch-time
override required for their automatic Remote Control reconnect.

**Created:** 2026-08-01

**Task:** `AI-CLI-165` (AIH-352 follow-up)

## Symptoms

Launching a previously Remote-Control-paired Claude Code session with `ai c` did not reconnect
automatically. The user had to use the manual pairing flow for each new or restarted session.

## Reproduction

1. Generate the Claude launch template for a worktree that has a `.claude` directory but no
   `.claude/.ai-cli-growthbook-toggle` file.
2. Execute the template's real pre-launch override block in that worktree.
3. Observe that `.claude/settings.local.json` is not created, so its
   `env.DISABLE_GROWTHBOOK` override is absent before Claude Code starts.

The frozen regression test reproduced this deterministically before the fix:

```text
tests/test_growthbook_launch_toggle.py::test_given_no_marker_when_toggle_runs_then_settings_local_json_gets_the_override
AssertionError: assert False
```

The failed assertion was `settings_path.exists()`, which is the missing pre-launch filesystem
effect, not a mock expectation.

## Root Cause Analysis

The generated launcher checked both that the engine was Claude Code and that the worktree carried
the marker file before writing the override. The marker was absent from all checked project
directories, so the launcher skipped the write for every ordinary launch.

That causes the visible failure directly:

```text
launch without marker
  -> marker-gated branch skips settings.local.json write
  -> DISABLE_GROWTHBOOK override is absent before Claude Code starts
  -> Remote Control's startup reconnect is ineligible
```

History shows that the marker gate was restored by a direct revert of an earlier unconditional
write. The repository's commit message, notes, changelog, and matching history provide no
explanation for the revert beyond naming the reverted commit. The related design record documents
the reason outside this repository: it was a temporary safety rollback until automatic restoration
of the override was available, to avoid leaving task tools disabled. That automatic restoration is
now documented as fixed, so the temporary marker gate no longer matches the intended launch
contract.

The suspicious generic commit identity is recurring across nearby history, not unique to either
the unconditional change or its revert. It therefore does not establish that those two commits
were accidental or automated test artifacts; no local history evidence establishes the author's
intent.

## Fix

Removed the marker-file predicate from the Claude Code branch in the generated session template.
The write remains worktree-scoped and remains excluded for the Gemini engine. It still merges only
`env.DISABLE_GROWTHBOOK` into `.claude/settings.local.json`.

## Verification

- Frozen RED before the production edit: `.venv/bin/python -m pytest
  tests/test_growthbook_launch_toggle.py -q` — 1 failed, 4 passed; the no-marker settings file
  was absent.
- GREEN after the production edit: the same command — 5 passed.
- The test executes the extracted generated Bash and Python snippet in a real subprocess against a
  temporary worktree; it asserts the resulting JSON file and value.

## Lessons Learned

A temporary safety rollback needs a tracked re-land condition and a test for the intended default
state. The prior test suite encoded the marker-gated behavior, so it could not detect that the
marker was absent fleet-wide or that the launch contract had changed.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

## Fix Log

| Date | Notes |
|---|---|
| 2026-08-01 | Removed the marker predicate after freezing a no-marker regression test RED. |

<!-- /doc:region name="fix_log" -->
