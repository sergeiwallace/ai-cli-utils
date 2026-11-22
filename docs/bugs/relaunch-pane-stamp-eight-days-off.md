---
title: "Relaunch pane lines reported with a stamp eight days and two hours off"
category: bug
tags: [bug, sessions, output]
status: not-reproduced
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Relaunch pane lines reported with a stamp eight days and two hours off

## Symptoms

After an automatic restart on a macOS host (iTerm2, zsh, tmux), lines pasted from the
pane read `2026-11-13 22:52:14 EST [session] direnv: Warning: ...` and
`... [internal] resume: background resolver exited 1; resuming by title instead`,
while the real time was 2026-11-21 about 20:52 EST.

## Reproduction Steps

Not reproduced. The regression tests run the child body the way its supervisor runs it
and check every stamped pane line, and every launch-log line, against the clock of the
run, through both stamp sources the child body has:

```sh
env -u AIH_LAUNCH_REASON uv run pytest -n 0 tests/test_worktree_envrc_approval.py -k "configured_timezone or fresh_unapproved or resume_notice"
```

## Root Cause Analysis

No code path in the session script produces a stamp other than the clock of the line.
Every stamp source it has was checked:

- `_ai_emit` replies with the line `ai internal emit` renders, whose record time is
  `datetime.now(UTC)` when the record is built (`output._timestamp`).
- When the renderer cannot run, `_ai_emit` stamps its plain line with `date`, run at
  that moment. It used a hard-coded `TZ=America/New_York`, which ignored the configured
  zone (a separate defect, fixed: the fallback now uses the configured format and zone);
  that put a line in the wrong zone, never on the wrong day.
- Lines from other `ai internal` actions (the resume lookup, the `.envrc` approval) are
  records built when they are emitted, the same as above.
- A record relayed from a child `ai` keeps the child's emit time, which is also the
  clock of that line.
- The template uses no `printf '%(...)T'`, `EPOCHSECONDS` or `EPOCHREALTIME`, which
  macOS's bash 3.2 lacks, and bakes no time into the generated script.

The reported timeline also rules out this restart as the source. The old agent exited
at 20:51:58 (logged), its child body ended after the 3-second restart window, and the
replacement child body started at 20:52:27, logging its first line at 20:52:54. A line
stamped 22:52:14, or 20:52:14 if only the hour were wrong, falls inside neither child
body's run. The lines were most likely older pane content shown again: restored
terminal scrollback, or a resumed conversation that carried them. Both message texts
already existed on 2026-11-13.

A live check on the host that reported it: in a pane of a session started after the
fix, run `date '+%F %T %Z'`, then `ai internal emit session info probe terminal probe`,
and compare the two stamps.

If it recurs, the launch log now settles it: every line a restart prints reaches the
log with the time it was printed, so a pane line with no matching log line was not
printed by that restart.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
