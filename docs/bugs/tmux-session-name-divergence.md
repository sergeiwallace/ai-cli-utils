---
title: "Session custom title and tmux name divergence investigation"
category: bug
tags: [bug, session, tmux, naming]
status: not-reproduced
severity: P2
template_version: "bug-1.0.0"
---

# Session custom title and tmux name divergence investigation

**Status:** not reproduced

**Created:** 2026-08-18

<!-- doc:region name="summary" kind="replaceable" -->

## Summary

An observed session custom title ended in `-1` while its tmux session name
appeared to end in `-0`, and another observed tmux name appeared to omit a
non-numeric custom-name segment. The current source did not reproduce either
outcome for `ai c fw`.

## Reproduction

The tmux boundary was mocked so no live tmux server was queried. With no
sessions, `build_session_name("c", "myproject", "fw")` returned
`c-myproject-fw-1` and `myproject-fw-1`; the equivalent hyphenated project
prefix also retained `fw`. When `c-my-cli-fw-1` was occupied, both returned
names advanced together to `c-my-cli-fw-2` and `my-cli-fw-2`.

The available source history was also inspected. Its index allocator starts at
1, and the non-numeric-name path constructs both returned values from the same
normalized name and index. The history available in this clone contains no
`i = 0` allocator or name-splitting operation that could drop `fw`.

## Root Cause Analysis

Not established. Current `cmd_c`/`cmd_g` call `build_session_name()` once,
then pass its `session_id` to `tmux new-session` and its `ai_name` to the
engine's `--name`; no later tmux rename path exists. Template refresh also
reuses persisted metadata. Consequently, the reported divergence cannot be
produced by the inspected current or available historical naming paths. A
future investigation needs the original session creation time, package version,
and the exact session name from the tmux server at that time; an already-created
tmux session cannot be retroactively renamed by this code.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
| --- | --- | --- | --- |
| 1 | 2026-08-18 | Isolated mocked-tmux reproduction and source/history trace | Not reproduced; no production change attempted. |

## Fix

None. No causal defect was confirmed, so no speculative production patch or
regression test was added.

## Verification

- Isolated Python reproduction: passed; the returned session and custom names
  were aligned for empty and occupied slots.
- `uv run pytest tests/test_session.py -q`: not run to completion. The stated
  `.venv` was absent; uv's default cache is read-only in this environment, and
  an isolated offline cache could not provide `pillow==12.3.0`.
- The full ruff/pytest gate has the same unavailable-environment dependency and
  was not claimed as passing.

## Lessons Learned

Session creation is the only point that can set a tmux session name. Preserve
the creation-time version and raw tmux observation when reporting a naming
issue; later custom-title observations alone cannot establish the name that was
initially allocated.

## Fix Log

| Date | Commit | Notes |
| --- | --- | --- |
| 2026-08-18 | — | Documented the unreproduced report and evidence gap; no code change. |

<!-- /doc:region name="summary" -->
