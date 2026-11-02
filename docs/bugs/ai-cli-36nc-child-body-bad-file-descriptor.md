---
title: "[AI-CLI-36nc] A restarted session prints `3: bad file descriptor` when its supervisor predates the script's launch log"
category: bugs
tags: [bug, session-script, supervisor, tmux, stderr, launch-log]
status: fix-in-review
severity: P1
task: AI-CLI-36nc
---

# [AI-CLI-36nc] Child body restores stderr from a descriptor its supervisor never opened

**Status:** fix-in-review

**Severity:** P1

**Created:** 2026-11-02

**Task:** `AI-CLI-36nc`

## Symptoms

When the supervisor of a tmux session started a new child body (an agent restart or
a hot reload), the pane printed:

```text
<state>/ai-cli-utils/sessions/<session>.sh:523: 3: bad file descriptor
```

The agent still started, so the line looked like noise, but it repeated on every
restart of that session for the rest of the supervisor's life.

A second symptom from the same cause had no visible error: a child rendered without a
launch log, under a supervisor that had one, left its agent's stderr in the
supervisor's launch-log relay, so agent errors reached the log and showed on the
terminal prefixed `[session] session:`.

## Environment

Linux, zsh 5 as the session shell, tmux 3.7, ai-cli-utils 0.8.0 at `6e56d5a6`. The
same text runs under bash 3.2 on macOS and fails the same way.

## Root cause

Two processes each decide half of one descriptor contract, from different
generations of the same file.

- The supervisor is the pane leader and lives for the whole session. When its own
  script carries a launch log path it runs `exec 3>&2` to save the terminal on fd 3,
  then points fd 2 at a relay that writes the launch log.
- Each child body is started from the stable script as it is on disk now, and
  restored the terminal with `exec 2>&3` whenever *its own* text carried a launch log
  path.

An `ai c` that attaches to a running session rewrites the stable script and leaves
the supervisor running. So the supervisor and its next child can come from different
renders:

```text
supervisor rendered with _ai_launch_log=''        (no fd 3, fd 2 is the terminal)
  -> attaching `ai c` rewrites the script with a launch log path
  -> agent exits, supervisor starts a child from the new text
  -> child runs `exec 2>&3`, fd 3 is not open
  -> shell prints `<script>:523: 3: bad file descriptor`, carries on with fd 2 unchanged
```

Evidence from the live host where it was reported:

- The pane leader's executing script (read through `/proc/<pid>/fd`, the deleted
  inode it still holds) contains `_ai_launch_log=''`; `/proc/<pid>/fd` has no fd 3,
  and fd 2 is the pty.
- The on-disk script for the same session names a launch log and has `exec 2>&3` at
  line 523, the line in the error.
- The child started at the reported restart time; its fd 2 is the pty, matching a
  failed `exec 2>&3` that the shell reports and survives.
- A second session on the same host shows the same mismatch (supervisor without fd 3,
  script with a log), so it would print the line on its next restart.

### Hypotheses

| Hypothesis | Prediction | Check | Result |
|---|---|---|---|
| A git-tracked session-status file is unreadable | An error naming that file's path | The message names the session script and fd 3 | Rejected |
| A Claude Code or hook error | Message in a transcript or hook log | Text is the zsh redirect-failure format with the script's own line number | Rejected |
| Child keys fd 3 on its own text, supervisor opened none | Supervisor without fd 3, script with a log, error at the `exec 2>&3` line | `/proc` fds and both script texts | Confirmed |

Why the supervisor was rendered without a launch log in the first place is not
established: it was started from a script already on disk when the host rebooted.
The fix does not depend on it, because any pairing of generations now works.

## Reproduction

`tests/test_supervisor_child_stderr.py` runs the real generated script on a pty, then
replaces the stable script with the other generation through
`_write_launch_script_if_changed` (the writer an attaching launch uses) and lets the
first agent exit, so the supervisor starts a child from the new text. Run on the
unfixed code, both shells:

```text
.../state/ai-cli-utils/sessions/test-session.sh:523: 3: bad file descriptor
AssertionError: ... '[session] session: agent-stderr 2' ...
4 failed
```

## Fix

The child decides from the descriptor its running supervisor holds, not from its own
launch log path. A bracketed block before the existing restore:

- fd 3 open: `exec 2>&3`, the terminal the supervisor saved;
- fd 3 closed: `exec 3>&2`, since fd 2 already is the terminal, so the existing
  log-keyed `exec 2>&3` below becomes a no-op.

The block is bracketed `# BEGIN RUN STATE` / `# END RUN STATE` and the original
lines stay byte-identical, because `ai restart` certifies a running program by full
text comparison with those brackets removed. Editing the original line instead made
every running pre-feature supervisor uncertifiable (`supervisor-upgrade-required` in
`tests/test_restart_supervisor.py`).

Every writer of a session script renders through `get_engine_script`: the launch
itself, `ai update`'s refresh of every live session (`_refresh_live_session_scripts`),
`ai internal write-stable-script`, `ai internal refresh-template`, the live-session
heal in `session_enrol`, and `ai restart`'s refresh. So every future script carries
the fix, and an existing live session picks it up once `ai update` regenerates its
script: its next child runs the fixed body under the unchanged supervisor, with no
supervisor restart.

## Verification

- Frozen regression tests: 4 failed before the fix, 4 passed after, zsh and bash.
- `tests/test_restart_supervisor.py`, `tests/test_restart_plan_contract.py`, and the
  reload, sinks, terminal-handoff, bash 3.2, plugin-refresh and launch-logging suites
  pass with the fix.

## Lessons Learned

- A descriptor opened by one process and used by another is a contract between two
  generations of a script, so test for the descriptor itself, not
  for the condition that once implied it.
- The existing supervisor tests render one script and run it unchanged, so no test
  ever started a child from a different render than its supervisor, which is the
  normal state of any long-lived session after an attaching launch.

## Fix Log

| Date | Change | Result |
|---|---|---|
| 2026-11-02 | Keyed the restore on fd 3 by editing the original line | Regression tests green; `ai restart` certification of running supervisors broke |
| 2026-11-02 | Bracketed insertion before the unchanged original lines | Regression and restart-certification suites green |
