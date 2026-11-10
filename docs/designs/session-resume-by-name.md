---
title: "Resuming a Session by Name: Design Document"
category: design
tags: [design, session-management, session-names]
status: active
template_version: "design-1.0.0"
---

<!-- doc:region name="overview" kind="replaceable" -->

# Resuming a Session by Name: Design Document

**Status:** ACTIVE

**Created:** 2026-11-10

Platform context: [architecture.md](architecture.md).

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
  - [What a resume reaches](#what-a-resume-reaches)
  - [The remote case](#the-remote-case)
  - [A positional name without -r](#a-positional-name-without--r)
  - [Sessions named before the registry](#sessions-named-before-the-registry)
- [Integration](#integration)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
- [Approval Log](#approval-log)

## Executive Summary

`ai c NAME -r` (and `-R -r` for the remote host) reaches the existing session NAME names,
or refuses before anything is reserved or created. NAME may be the session's full title,
its first name, or a legacy index title from before the session-name registry. A running
tmux session is attached; a stopped one is relaunched under its own title from the
worktree and transcript it left. A NAME no session holds exits 1 with the closest
existing titles and the command that starts a new session instead.

## Problem Statement

Measured on 2026-11-10, two launches meant to resume an existing chief session were typed
as `ai c cos -R -r` from a Mac launcher (AI-CLI-e8b1). No session held `cos`. Each one:

1. asked the remote allocator (`ai internal allocate-session-name`) for a session without
   saying it was a resume, so the allocator read `cos` as a label and reserved a new first
   name (`...-cos-hal`, then `...-cos-rae`);
2. handed the remote side `--resume <that title>`, which found the row this machine had
   just written, logged "no tmux session runs ...; relaunching it under the title it
   holds", and began creating a worktree for a name minted seconds earlier.

Locally the same gap had three more shapes: a stopped session with an index title was
refused by `-r` ("drop -r/--resume to start it"), a running session typed by its full
title was looked up with the naming prefix applied twice, and `-r --dry-run` planned a
brand-new session. `ai locate-session` answered "no session holds" for any session named
before the registry, so the name tools could not say how to reach one.

## Design Overview

### What a resume reaches

`_resolve_resume_target` in `src/ai_cli/main.py` decides the target once, above the
dry-run exit and above every write the launch makes. In order:

1. A running tmux session, local (`c-<title>`) or remote-launched (`c-r-<title>`): the
   full tmux name as given, else the title reduced to its slot, else the pre-host-alias
   form `c-<repo-alias>-<slot>`.
2. A stopped session, proved by evidence on disk: its worktree under
   `<repo>/.worktrees/<title>` or, for Claude Code, a transcript titled `<title>`. The
   titles tried are the one this machine holds for NAME in the registry (so `ai c ada -r`
   finds `myrepo-box-review-ada`), the one NAME composes, and NAME's pre-host-alias form.
3. Nothing: exit 1 with no reservation, worktree, branch, transcript or remote
   allocation. The refusal lists up to five close titles from this host's tmux sessions
   and worktrees and the command that starts a new session (`ai c NAME`, or
   `ai c --name NAME` for a first name).

A registry that cannot be read is a refusal carrying the read error, never "nothing is
held" followed by a new name. An unreachable registry remote falls back to the last
fetched tip, the same as every other reader.

A dry run makes the same decision: it plans `attach to tmux session ...`, a relaunch of
the stopped title, or refuses with the same message.

### The remote case

With `-R -r` the launcher passes `--resume` to `ai internal allocate-session-name`. Under
`--resume` the allocator runs the same resolver on the remote host against the project
directory and answers only with an existing session; it never reserves. A refusal, or an
ssh failure, ends the launch with exit 1 before the transport hand-off. The remote side's
own `--resume` then repeats the decision, so a title that has a row but no session (the
`rae` case) is refused there too, with the remote-refusal exit 78 the reconnect ladder
already stops on.

A remote dry run still contacts no host, so it cannot resolve the remote session; the
remote host decides at launch.

The chief of staff keeps its own resolution (`ai cos -r`, `ai cos -R -r`, or
`ai c <chief title> -R -r`), which already attaches, resumes or refuses on the machine the
chief runs on.

### A positional name without -r

Without `-r`, a positional NAME that is not an existing title is still the label of a new
session (`ai c cos` starts `myrepo-box-cos-rae`). The launch now says so in its session
line, `new session <title>: 'cos' is not the title of an existing session, so it labels a
new one; to resume an existing session instead, run ai c TITLE -r`, before it creates a
worktree or a tmux session. The note appears when the resolved title does not end in what
was typed.

### Sessions named before the registry

`unregistered_sessions` in `src/ai_cli/session_locate.py` lists this host's sessions that
no registry row names: index titles with a worktree under `<projects>/<repo>/.worktrees/`
or a live tmux session. `ai locate-session TITLE` reports one as an unregistered session
with its state, worktree and resume command (`ai c TITLE -r -p REPO`) and exits 0;
`ai list-names` adds a `no name` line for each. `ai list-names --json` still lists
registry rows only.

## Integration

- `ai c -h` and the `-r/--resume` help text say how to resume by name.
- README § "Resuming an existing session by name" and `docs/tools/ai-cli-usage.md` § ai c
  carry the command forms, including `-R` and the chief.
- Tests: `tests/test_resume_by_name.py` drives every case through `cli()` against a real
  name registry with a fake tmux at the process boundary.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A session that ran with `-W/--no-worktree` and left no Claude Code transcript has no evidence, so `-r` refuses it after it stops | The refusal names the command that starts it again; a running one is still attached through tmux |
| Close-match suggestions list a title from another engine's session in the same repository | Suggestions are hints only; resolution never uses them |
| A remote dry run cannot show the refusal | Documented; the real launch refuses before any allocation |

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | What proves a stopped session exists | (a) worktree or transcript, (b) a registry row held here, (c) either | (a) | (a) | No | A row is also what a reservation made seconds earlier looks like | `Resolved` |
| D-2 | A positional NAME without -r that is no existing title | (a) say "new session" before creating, (b) require an explicit new-session flag | (a) | (a) | No | Smallest change; `ai c LABEL` is documented behaviour | `Resolved` |
| D-3 | Legacy sessions in `ai list-names --json` | (a) leave JSON as registry rows, (b) add synthetic rows | (a) | (a) | No | Consumers read `token` and `kind` from every row | `Resolved` |

### Decision Details

<a id="d-1"></a>

#### D-1: What proves a stopped session exists — ✅ Resolved by Claude Opus 5.5: (a) worktree or transcript

**Context.** The old relaunch trusted a registry row held by this machine. The allocator
writes that row before the session's worktree exists, so the remote side "resumed" a
title minted for a typo.

- **(a) Worktree or Claude Code transcript.** Pros: both exist only once a session ran;
  they are what a relaunch resumes. Cons: a `-W` session with no transcript is not
  resumable after it stops.
- **(b) Registry row.** Pros: covers `-W` sessions. Cons: is the bug.
- **(c) Either.** Cons: inherits (b)'s failure.

**Recommendation.** (a). The `-W` cost is mitigated by the refusal naming the new-session
command.

<a id="d-2"></a>

#### D-2: A positional NAME without -r — ✅ Resolved by Claude Opus 5.5: (a) say "new session"

**Context.** `ai c review` creating `myrepo-box-review-ada` is documented and relied on,
so refusing it would break the label convention.

- **(a) Announce.** Pros: no behaviour change for labels; the typo is visible before
  anything exists. Cons: the reservation is made before the line prints, since the name is
  picked then; nothing else is created first.
- **(b) Require a flag.** Pros: no accidental session. Cons: changes every labelled launch.

**Recommendation.** (a).

<a id="d-3"></a>

#### D-3: Legacy sessions in JSON output — ✅ Resolved by Claude Opus 5.5: (a) unchanged

- **(a) Unchanged.** Pros: no consumer breaks. Cons: JSON readers do not see legacy
  sessions; `ai locate-session TITLE -j` does report one.
- **(b) Synthetic rows.** Cons: rows without a token break readers that key on it.

**Recommendation.** (a).

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-11-10 | session-resume-by-name/D-1 | Claude Opus 5.5 | Evidence is a worktree or transcript; fresh-authoring, high confidence. |
| 2026-11-10 | session-resume-by-name/D-2 | Claude Opus 5.5 | Announce the new session; fresh-authoring, high confidence. |
| 2026-11-10 | session-resume-by-name/D-3 | Claude Opus 5.5 | JSON unchanged; fresh-authoring, medium confidence. |

<!-- /doc:region name="overview" -->
