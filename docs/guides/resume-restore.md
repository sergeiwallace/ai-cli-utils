---
title: "Restoring an Archived Transcript Before a Resume"
category: guide
tags: [resume, transcripts, config, hooks]
status: current
source: internal
---

# Restoring an Archived Transcript Before a Resume

Some deployments save disk by archiving Claude Code transcripts that have been idle for a
while, for example by compressing `<session-id>.jsonl` to `<session-id>.jsonl.zst` and
removing the original. `claude --resume` cannot read an archive, and `ai c` cannot see an
archived conversation when it picks which one to resume. The `[resume_restore]` setting
names a command of your own that puts the transcript back before every resume.

With no command configured, a resume works exactly as before and nothing extra runs.

## Configuration

In `config.toml` (see [Tool Config](../../README.md#tool-config) for where it lives):

```toml
[resume_restore]
command = ["restore-transcript", "--session-id", "{session_id}"]
timeout_seconds = 300
```

`command` is an argument list, run without a shell. `{session_id}` in any argument is
replaced by the session's id, so at least one argument must contain it. A bare first
element is looked up on `PATH`; give a full path to run a program that is not on it.
`timeout_seconds` defaults to 300. Both are read on every resume, so a change applies at the
next one, including a running session's next automatic restart.

Point `command` at whatever restores your archives. The command must:

- restore the session's transcript, and any sub-agent transcripts it keeps under
  `<session-id>/`, to the paths Claude Code reads them from;
- exit 0 when everything was restored or nothing needed restoring (including a transcript
  that is already in place);
- exit non-zero when a restore failed, leaving the archive in place.

## When it runs

Before every Claude Code resume: `ai c NAME`, the automatic restart inside a tmux session,
a pinned restart and a bare (`-b`) launch.

1. Before the resume target is chosen, once for each `<session-id>.jsonl.zst` in the
   session's project directory whose `<session-id>.jsonl` is missing. Afterwards the
   transcript must exist; a command that exits 0 without restoring it stops the resume. A
   restored transcript keeps the archive's modification time when the command gave it a
   newer one, so an old conversation does not become the most recently active one.
2. For the session about to be resumed, so its sub-agent transcripts are restored too.

## When it fails

A non-zero exit, a timeout or a command that cannot start stops the resume before Claude
Code starts. The launcher prints the session id, the exit status (or the timeout), the
command and its output. In a tmux session the pane stays open: fix the cause, then type
`exit 78` there or run `ai c NAME` again. A bare launch exits 1.

A setting that is present but unusable (not a list, no `{session_id}`, a non-positive
timeout) also stops the resume, naming the setting.

## Checking it from a script

`ai list-capabilities` prints `transcript-restore` on a line of its own when a valid
`command` is set, so a tool that archives transcripts can check that every launcher on the
machine restores them before it archives anything.

## Windows

The command runs without a shell on every platform. A `.cmd` or `.bat` program on `PATH` is
found by its bare name; anything else that needs a shell must be wrapped explicitly, for
example `["cmd", "/c", "restore.cmd", "{session_id}"]`.
