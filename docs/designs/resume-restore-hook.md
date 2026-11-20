---
title: "Pre-Resume Transcript Restore: Design Document"
category: design
tags: [design, session-management, resume, transcripts]
status: active
template_version: "design-1.0.0"
---

<!-- doc:region name="overview" kind="replaceable" -->

# Pre-Resume Transcript Restore: Design Document

**Status:** ACTIVE

**Created:** 2026-11-20

Platform context: [architecture.md](architecture.md). User guide:
[resume-restore.md](../guides/resume-restore.md).

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
- [Integration](#integration)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
- [Approval Log](#approval-log)

## Executive Summary

`[resume_restore] command` in `config.toml` names a program the launcher runs before every
Claude Code resume, with `{session_id}` replaced by the session's id. Exit 0 lets the resume
go ahead; any other result stops it before Claude Code starts and prints the command's
output and exit status. Unset, nothing runs. A deployment that archives idle transcripts
points it at its own restore tool.

## Problem Statement

A deployment that compresses an idle `<session-id>.jsonl` to `<session-id>.jsonl.zst` and
removes the original breaks two things in the launcher. `claude --resume <id>` cannot read
the archive. Before that, the resolver that picks the conversation to resume globs
`*.jsonl` and reads each transcript's title and modification time, so an archived
conversation is invisible: `ai c NAME` starts a fresh conversation, and a pinned restart
refuses because its recorded transcript is missing. Sub-agent transcripts under
`<session-id>/` can be archived while the main transcript is still active.

## Design Overview

`src/ai_cli/transcript_restore.py` reads the setting at call time and runs the command.

- `restore_archived(project_dir)` runs it for each `<uuid>.jsonl.zst` with no
  `<uuid>.jsonl` beside it, checks the transcript now exists, and lowers its mtime to the
  archive's when the command left it newer.
- `restore_session(session_id)` runs it for the session about to be resumed.

Call sites:

| Path | Before choosing | Before `claude --resume` |
|---|---|---|
| tmux session script (first start, auto-restart, pinned restart) | `ai internal resolve-continue-target` calls `restore_archived`; failure exits 74; `_resolve_cc_transcript` passes 74 through on a restart (`TRANSCRIPT RESTORE restart`) and the script stops (`TRANSCRIPT RESTORE resolve-failed`) | the script runs `ai internal restore-transcript <id>`; failure stops it (`TRANSCRIPT RESTORE session`) |
| bare launch (`_bare_engine_command`) | `restore_archived` | `restore_session` for the matched id; `_do_session_launch` prints the error and exits 1 |

`ai list-capabilities` prints `transcript-restore` when a valid command is configured.

## Integration

- `config.toml` template: the commented `[resume_restore]` section.
- Session script: both insertions are bracketed `TRANSCRIPT RESTORE`, and the restart
  compatibility check in `session_restart.py` drops that bracket family, so the supported
  pre-feature program is still certified.
- Tests: `tests/test_resume_restore_hook.py` drives the bare launch and the generated tmux
  wrapper with fixture `tmux`, `ai` and `claude` programs and a fixture restore command.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Restoring an old conversation gives it a fresh mtime, so it outranks the one the user meant | The restored file is set back to the archive's mtime when it came back newer |
| A command that exits 0 but restored nothing resumes onto a missing file or a fresh conversation | `restore_archived` checks the transcript exists and stops the resume otherwise |
| A mistyped setting silently disables the restore | A present but invalid setting stops the resume and names the key |
| A hung command blocks a restart forever | `timeout_seconds` (default 300) ends it and stops the resume |
| One extra `ai internal` process per Claude Code start | It returns at once with no command configured; accepted |

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | Config key and command form | (a) `[resume_restore] command` list, (b) a string split with `shlex` | (a) | (a) | No | Matches `[extensions.run.*] command`; no quoting rules differ by OS | `Resolved` |
| D-2 | Placeholder | (a) literal `{session_id}` replaced in every argument, (b) `str.format` | (a) | (a) | No | Other braces in arguments pass through untouched | `Resolved` |
| D-3 | When it runs | (a) for archives before resolving and for the resumed id, (b) for the resumed id only | (a) | (a) | No | (b) never sees an archived conversation, which is the case the hook exists for | `Resolved` |
| D-4 | Timeout | (a) 300 s default, configurable, (b) none | (a) | (a) | No | A restart must not hang on a stuck tool | `Resolved` |
| D-5 | How a failure stops the tmux path | (a) emit, record the exit and stop the child loop, (b) exit the session | (a) | (a) | No | The pane stays open showing the output; `exit 78` retries | `Resolved` |
| D-6 | Probe command name | (a) `ai list-capabilities`, (b) `ai capabilities` | (a) | (a) | No | The CLI names commands verb-noun | `Resolved` |

### Decision Details

<a id="d-1"></a>

#### D-1: Config key and command form — ✅ Resolved by Claude Opus 5.5: (a) argument list

- **(a) `[resume_restore] command = [...]`, `timeout_seconds`.** Pros: the same shape as
  launch extensions; run with `subprocess.run(list)` and no shell on every OS. Cons: a
  shell pipeline needs an explicit `sh -c` or `cmd /c` wrapper.
- **(b) A command string.** Cons: `shlex` splitting is POSIX-only; Windows paths with
  backslashes need different quoting.

**Recommendation.** (a). A bare first element is resolved with `shutil.which`, so a
Windows `.cmd` on `PATH` is found by name.

<a id="d-2"></a>

#### D-2: Placeholder — ✅ Resolved by Claude Opus 5.5: (a) literal `{session_id}`

- **(a) Literal replacement.** Pros: an argument such as a JSON fragment keeps its other
  braces. Cons: none found.
- **(b) `str.format`.** Cons: any other brace raises or is consumed.

**Recommendation.** (a). A command with no argument containing the placeholder is refused,
since it cannot know which session to restore.

<a id="d-3"></a>

#### D-3: When it runs — ✅ Resolved by Claude Opus 5.5: (a) both points

**Context.** The resolver chooses the conversation by title and mtime, which it cannot read
from an archive, so restoring only the chosen id would never restore an archived one.

- **(a) Before resolving, for each archived main transcript in the session's project
  directory; then for the resumed id.** Pros: the archived conversation becomes visible;
  sub-agent archives of an active session are restored. Cons: every archived conversation
  in that one directory is restored, not just the one resumed.
- **(b) The resumed id only.** Cons: misses the main case.

**Recommendation.** (a). The project directory is one worktree's, so it rarely holds more
than a few conversations; the archive tool archives them again once idle.

<a id="d-4"></a>

#### D-4: Timeout — ✅ Resolved by Claude Opus 5.5: (a) 300 s default

- **(a) 300 s, `timeout_seconds`.** Pros: generous for a large transcript with sub-agents;
  a stuck tool ends. Cons: only the direct child is killed at the timeout.
- **(b) None.** Cons: a hung tool parks the session with no message.

**Recommendation.** (a).

<a id="d-5"></a>

#### D-5: How a failure stops the tmux path — ✅ Resolved by Claude Opus 5.5: (a) stop the loop

- **(a) Emit an error, record the exit reason, break the child loop.** Pros: the same path
  as the other refused starts; the local supervisor hands the pane to a shell, so the
  command's output stays visible. Cons: none found.
- **(b) Exit the tmux session.** Cons: the output disappears with the pane.

**Recommendation.** (a). A resolver failure exits 74, a status no other path uses.

<a id="d-6"></a>

#### D-6: Probe command name — ✅ Resolved by Claude Opus 5.5: (a) `ai list-capabilities`

- **(a) `ai list-capabilities`.** Pros: follows the verb-noun naming rule. Cons: a probe
  written for `ai capabilities` must be pointed at it.
- **(b) `ai capabilities`.** Cons: breaks the naming rule.

**Recommendation.** (a).

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-11-20 | resume-restore-hook/D-1 | Claude Opus 5.5 | Argument list; fresh-authoring, high confidence. |
| 2026-11-20 | resume-restore-hook/D-2 | Claude Opus 5.5 | Literal placeholder; fresh-authoring, high confidence. |
| 2026-11-20 | resume-restore-hook/D-3 | Claude Opus 5.5 | Restore before resolving and for the resumed id; fresh-authoring, high confidence. |
| 2026-11-20 | resume-restore-hook/D-4 | Claude Opus 5.5 | 300 s default; fresh-authoring, medium confidence. |
| 2026-11-20 | resume-restore-hook/D-5 | Claude Opus 5.5 | Stop the child loop; fresh-authoring, high confidence. |
| 2026-11-20 | resume-restore-hook/D-6 | Claude Opus 5.5 | Verb-noun probe name; fresh-authoring, high confidence. |

<!-- /doc:region name="overview" -->
