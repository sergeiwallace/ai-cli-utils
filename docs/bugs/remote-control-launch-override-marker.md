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

## 2026-08-15 Follow-up Investigation

**Status:** the original marker-gate fix remains deployed. No ai-cli-utils production change was
made in this follow-up because the remaining automatic-reconnect report could not be reproduced in
the available non-interactive environment.

### Reproduction and environment

- Revision investigated: `d6bbb77`.
- Installed Claude Code: `2.1.233`; the prior investigation used `2.1.226`.
- A generated Claude launch template was traced and its actual pre-launch Bash/Python block was
  executed in a temporary worktree. Before any `run_agent claude` call, it created
  `.claude/settings.local.json` containing `env.DISABLE_GROWTHBOOK: ""`.
- The isolated-worktree initializer symlinks the repository's `.claude` directory when it exists,
  so the normal `ai c` worktree path supplies the directory required by that write.
- The launcher history after `3254431` contains no change to the pre-launch override block. The
  SessionStart restorer remains registered in the installed user settings and in the hook manifest;
  it starts only after Claude Code has started, so it cannot prevent the pre-launch write.
- The focused pytest suite could not be run in this sandbox: its missing development environment
  required a package download, and DNS/network access is unavailable. The existing test remains a
  real subprocess/filesystem test of the generated block, rather than a mock or a copied snippet.

### Manual `/rc` root cause

The reported long-running process had no worktree-local override and inherited the user-global
`DISABLE_GROWTHBOOK=1` setting. Static inspection of the installed 2.1.233 Claude Code client
contains the explicit eligibility error: Remote Control requires feature-flag evaluation and is
unavailable when `DISABLE_GROWTHBOOK` is set. Therefore `/rc` and `/remote-control` are correctly
rejected in that process; this is not an ai-cli-utils slash-command implementation failure.

```text
global DISABLE_GROWTHBOOK=1 + no local empty override in the running process
  -> Claude Code disables GrowthBook feature-flag evaluation
  -> Claude Code rejects Remote Control eligibility
  -> manual /rc fails
```

The pre-launch override cannot retroactively repair an already-running Claude process. A new
process must start with the local empty override already present.

### Automatic reconnect finding

The reproducible launcher-level portion is correct: the current generated template writes the
override before every normal tmux-path Claude invocation, including `--continue` invocations.
The available evidence therefore rejects a reintroduction of the old marker-gate root cause and
does not support a launcher patch.

The residual report is outside what this sandbox can verify: pairing a device, invoking the
interactive slash command, and starting a real attached `ai c` session are unavailable here. The
2.1.233 binary does contain a reconnect diagnostic that instructs the user to retry or start a
fresh session without `--resume`; ai-cli-utils normally uses `--continue` when resuming a matching
conversation. That makes the Claude Code resume/reconnect path a concrete external hypothesis,
not a confirmed release regression. No release-note or live-UAT evidence in this repository
identifies when that behavior changed.

```text
fresh launcher process for an existing conversation
  -> ai-cli-utils writes the GrowthBook override before Claude starts
  -> ai-cli-utils may invoke Claude Code with --continue
  -> remaining reconnect failure, if reproduced, is in Claude Code's resume/Remote-Control path
```

Required live UAT, outside this sandbox: launch a genuinely new `ai c` process for a previously
paired conversation, confirm the local override exists before the Claude process begins, then
confirm the phone reconnects. If it does not, capture the Claude Code version and the client error
while comparing a non-resumed fresh conversation with the `--continue` launch. That discriminates
the remaining resume-path hypothesis without changing the launcher.

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
| 2026-08-15 | Re-traced the generated launcher, inspected the installed SessionStart wiring and Claude Code 2.1.233, and made no speculative production change. Confirmed the manual `/rc` failure is the inherited `DISABLE_GROWTHBOOK=1` eligibility gate; automatic reconnect still requires live UAT to attribute beyond the launcher. |

<!-- /doc:region name="fix_log" -->
