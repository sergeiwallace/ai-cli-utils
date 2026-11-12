---
title: "Probe Sessions: Design Document"
category: design
tags: [design, session-management, worktrees]
status: active
template_version: "design-1.0.0"
---

<!-- doc:region name="overview" kind="replaceable" -->

# Probe Sessions: Design Document

**Status:** ACTIVE

**Created:** 2026-11-12

Platform context: [architecture.md](architecture.md).

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
  - [What a probe launch writes](#what-a-probe-launch-writes)
  - [Refused combinations](#refused-combinations)
  - [Removing a probe](#removing-a-probe)
- [Integration](#integration)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
- [Approval Log](#approval-log)

## Executive Summary

`ai c -t/--probe [NAME]` (and `ai g`, `ai p`, `ai cx`) launches a throwaway probe or test
session that nothing treats as a session home. It holds no first name, its worktree lives
outside the repository and is not recorded as a canonical session worktree, it is never
relaunched at boot or by the iTerm2 restore, and its run record is pruned once its session
is gone (`ai iterm2 sessions -p/--prune`).

## Problem Statement

A probe or test session started as an ordinary `ai c` slot was indistinguishable from a
real session, and every subsystem that protects session homes protected it too:

1. the launch reserved a first name for it in the shared name registry;
2. its worktree under `<repo>/.worktrees/<title>` was written to the canonical-worktree
   registry (`canonical-session-worktrees.json`), which deletion guards read as "this is a
   long-lived session home, never reap it";
3. its run record carried a boot recipe with relaunch policy `auto`, so
   `ai sessions relaunch --boot` replayed it after every restart;
4. being inside the repository, the worktree was picked up by editors that scan the
   workspace for repositories.

So a probe stayed around as if it were a canonical session, and removing it meant
reviewing it as one.

## Design Overview

### What a probe launch writes

`-t/--probe` is a launch option. `_probe_naming` in `src/ai_cli/main.py` turns NAME into
`probe` or `probe-NAME` and switches the launch to index naming, before the launch reads
anything:

| Launch state | Ordinary launch | Probe launch |
|---|---|---|
| Title | first name or index (`myrepo-box-ada`) | index under the `probe` label (`myrepo-box-probe-1`, `myrepo-box-probe-smoke-1`) |
| Name registry | a row reserved for the first name | nothing read or written |
| Worktree | `<repo>/.worktrees/<title>` | `<state>/probe-worktrees/<repo>-<digest>/<title>`, from `probe_worktree_base` in `src/ai_cli/session.py` |
| Canonical-worktree registry | entry added | nothing written |
| Run record | boot recipe, `relaunch: auto` | boot recipe, `probe: true`, `relaunch: never` |
| iTerm2 session tracking | recorded | not recorded |

`<state>` is the per-user state directory (`$XDG_STATE_HOME/ai-cli-utils`, or
`%LOCALAPPDATA%\ai-cli-utils` on Windows). The `<digest>` is the first eight hex digits of
a SHA-256 of the repository's resolved path, so two checkouts with the same name never
share probe slots. The branch is `wt-<title>` as for any session worktree, and the
worktree's `.envrc` is approved the same way as a `.worktrees/` slot's.

The run record keeps its boot recipe because the session supervisor refuses to start an
engine without one. `relaunch: never` makes `boot_veto` refuse it. `iterm2_restore`
skips every record marked `probe`. `session_registry.prune` (run by
`ai iterm2 sessions -p/--prune`) drops a probe record once tmux has no session for it. It keeps a bare probe (which has no tmux session) while the
record's process still runs in the current boot.

### Refused combinations

`-t/--probe` exits 1, before anything is read or written, with any of these:
`-r/--resume`, `-N/--name`, `-w/--with-name`, `-u/--no-name`, `-S/--session-names`,
`-F/--chief` (or `ai cos`, or a chief positional name) and `-R/--remote`. Each one would
name, reach or host a canonical session. `-W/--no-worktree` is accepted: the probe then
runs in the current directory and creates no worktree at all. `-d/--dry-run` plans the
probe title and writes nothing.

### Removing a probe

A probe worktree is scratch. Nothing records it as a session home, so the ordinary
worktree tools remove it once its session has exited:

```bash
git -C <repo> worktree remove <state>/probe-worktrees/<repo>-<digest>/<title>
git -C <repo> branch -d wt-<title>
```

`ai retire-session` is for session homes under `.worktrees/` and does not handle probes.
The launcher never removes a probe worktree itself: a probe may have produced work worth
keeping, and the person who ran it decides.

## Integration

- `ai c -h` names the probe form; `-t/--probe` has its own help line on every engine
  command, and `docs/reference/cli.md` is regenerated from it.
- README § "Probe and test sessions" and `docs/tools/ai-cli-usage.md` § ai c carry the
  command forms.
- Tests: `tests/test_probe_launch.py` drives `ai c -b -t` through `cli()` against a real
  name registry, a real git repository with an `origin`, and the real canonical-worktree
  and session registries (redirected per test), with tmux and the engine faked at the
  process boundary.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A probe worktree is still a git worktree, so `git worktree list` and any tool that reads it still show it until it is removed | It is outside the repository directory, so a workspace scan does not find it; removal is one `git worktree remove` |
| A crash leaves a tmux probe's record marked running | Prune decides from tmux, so the record goes once tmux has no session for it |
| A bare probe has no tmux session, so a tmux-only liveness test would prune it while it runs | Prune keeps a bare probe whose recorded process is alive in the current boot |
| `ai sessions relaunch NAME` named explicitly still replays a probe's recipe | It is an explicit request for that session; boot replay never does it |

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | How a launch becomes a probe | (a) a `-t/--probe` flag, (b) a reserved `probe` label, (c) a config setting | (a) | (a) | No | Explicit, per launch, and changes no existing launch | `Resolved` |
| D-2 | Where a probe worktree lives | (a) per-user state directory, (b) `.worktrees/` but unregistered, (c) no worktree | (a) | (a) | No | Outside the repository and away from every `.worktrees/` reader | `Resolved` |
| D-3 | A probe's run record | (a) keep it with `relaunch: never`, pruned when gone, (b) write none, (c) delete it at the exit transition | (a) | (a) | No | The supervisor needs the record; prune already removes dead records | `Resolved` |
| D-4 | A remote probe (`-t -R`) | (a) refuse, (b) teach the remote allocator about probes | (a) | (a) | No | `ai c -t` on the remote host gives the same result | `Resolved` |

### Decision Details

<a id="d-1"></a>

#### D-1: How a launch becomes a probe — ✅ Resolved by Claude Opus 5.5: (a) a `-t/--probe` flag

**Context.** A probe must be distinguishable at launch, before a name is reserved or a
worktree is made.

- **(a) A `-t/--probe` flag.** Pros: explicit; one launch at a time; removing the flag
  later changes nothing else. Cons: a probe launched without the flag is still an
  ordinary session.
- **(b) A reserved `probe` label routed to probe mode.** Pros: no new flag. Cons: changes
  what `ai c probe` does today, and a label typed for another reason turns into a probe.
- **(c) A config setting.** Cons: a setting cannot tell one launch from the next.

**Recommendation.** (a), the more reversible option. Its cost is mitigated by the `ai c -h`
text and the README naming the flag for probe and test work.

<a id="d-2"></a>

#### D-2: Where a probe worktree lives — ✅ Resolved by Claude Opus 5.5: (a) per-user state directory

**Context.** Session homes live in `<repo>/.worktrees/`, which editors scan and which
other tools read as session homes.

- **(a) `<state>/probe-worktrees/<repo>-<digest>/<title>`.** Pros: outside the repository;
  per user; the digest keeps same-named checkouts apart. Cons: harder to find than a
  sibling of the repository; documented in the README and the launch output.
- **(b) `.worktrees/<title>`, not registered.** Pros: no new location. Cons: still in the
  workspace scan, and anything that reads the directory layout instead of the registry
  still treats it as a home.
- **(c) No worktree (`-W`).** Cons: runs in the main checkout, which the launcher refuses
  for sessions because it breaks isolation.

**Recommendation.** (a).

<a id="d-3"></a>

#### D-3: A probe's run record — ✅ Resolved by Claude Opus 5.5: (a) keep it with `relaunch: never`

**Context.** The supervisor (`session_run_state.begin`) refuses to start an engine whose
record has no boot recipe.

- **(a) Keep the record, `probe: true`, `relaunch: never`; prune drops it once the session
  is gone.** Pros: lifecycle tracking unchanged; uses the existing veto and prune.
  Cons: the record exists while the probe runs and until the next prune.
- **(b) No record.** Cons: the supervisor refuses the launch.
- **(c) Delete the record at the exit transition.** Cons: races the supervisor's own exit
  writes, and a crash never reaches it.

**Recommendation.** (a). The leftover record is inert, since boot replay and the iTerm2
restore both skip it, and `ai iterm2 sessions --prune` removes it.

<a id="d-4"></a>

#### D-4: A remote probe — ✅ Resolved by Claude Opus 5.5: (a) refuse `-t -R`

**Context.** `-R` asks the remote host's allocator for a title, and that allocator reserves
first names.

- **(a) Refuse.** Pros: no remote state is written for a probe by accident. Cons: a probe
  on the remote host is launched there with `ai c -t`.
- **(b) Teach the allocator.** Cons: a remote protocol change for a case (a) already covers.

**Recommendation.** (a). The refusal names the flags it cannot combine with.

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-11-12 | probe-sessions/D-1 | Claude Opus 5.5 | Explicit flag; fresh-authoring, high confidence. |
| 2026-11-12 | probe-sessions/D-2 | Claude Opus 5.5 | Per-user state directory; fresh-authoring, medium confidence. |
| 2026-11-12 | probe-sessions/D-3 | Claude Opus 5.5 | Record kept, never relaunched, pruned; fresh-authoring, high confidence. |
| 2026-11-12 | probe-sessions/D-4 | Claude Opus 5.5 | Refuse a remote probe; fresh-authoring, medium confidence. |

<!-- /doc:region name="overview" -->
