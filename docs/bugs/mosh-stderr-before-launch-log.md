---
title: "Mosh hides a remote failure written outside the launch log"
category: bug
tags: [bug, sessions, mosh]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Mosh hides a remote failure written outside the launch log

## Symptoms

A remote refusal can disappear when mosh restores the terminal. The client can
then retry the refused command over SSH or suggest a session-name collision.

## Reproduction Steps

Run the remote shell wrapper with a command that writes a migration refusal to
stderr before creating a launch log and exits 78. Exercise the client transport
loop at one second and twelve seconds. The regression uses a real shell and
fixture files for the remote diagnostic read.

## Root Cause Analysis

The mosh wrapper depended on the remote launcher copying stderr into its own
diagnostic sink. Output before that sink exists never reaches the file. The
early transport failure branch also consumed the status file without using it.
A terminal replay alone cannot restore a line once the remote process is gone.

## Fix

The wrapper copies remote stderr to an owner-only diagnostic file while displaying it
live. It keeps stdout on the terminal and records the remote command's exit
status separately. The client reads that result before classifying a short
transport exit.

## Verification

`test_given_remote_stderr_before_a_launch_log_exists_when_mosh_ends_then_the_refusal_and_status_survive`
failed at both timings because the client output lacked the refusal and retried
over SSH. It now checks that the refusal and exact finish command are visible,
that the client exits 78, and that the collision hint is absent.

## Lessons Learned

Test stderr recovery with a process that writes straight to stderr, and test
exits under both of the short durations the transport treats specially.

## Agent exits after handoff

### Symptoms and reproduction

A tmux pane displays the agent's startup error and then closes. The owning
host's log ends with `Auto-restart stopped. Returning to the calling shell.`
and the launching host may report an ambiguous clean transport exit. To
reproduce, run the generated supervisor or one-shot handoff on a test PTY,
write a diagnostic to the agent's stderr, and exit the agent while it is still
starting. Exercise the remote wrapper with a successful transport exit.

### Cause

The supervisor restores the agent's terminal descriptors before launching
it. This preserves interactivity and sends its output past the launcher's
stderr mirror. The one-shot owner recorded the exit status alone. A tmux
client can return zero after the pane closes, so the remote wrapper's saved
command status cannot identify the agent exit. The Codex thread guard also
used the same supervisor exit status as a lifecycle persistence failure.

### Fix and verification

The shared exit recorder measures runtime before cleanup and saves a bounded
tail for startup exits. It reads the current tmux pane while it still exists
or uses bytes already relayed by the bare PTY. Pane capture reflects the
screen and scrollback tmux still has. The recorder also writes remote startup
diagnostics to a per-launch file that SSH and mosh can retrieve after the
connection closes. The Codex refusal continues to stop the supervisor and
now records its own reason in lifecycle evidence.

At revision `6e797f42`, the regression assertions failed for missing output
in the managed, one-shot, bare PTY and remote launch logs. The tests keep the
agent on a PTY and use fake agent and transport commands.

### Worktree sync warnings

The warning already reached the log, but selecting Git's final stderr line
could report `Applied autostash.` as the error. The worktree warning now
prefers Git's explicit error lines. The regression appends that progress
line after a real failed fetch and checks that the warning still reports
the error and the launch proceeds.

### Transport client stderr

The VPN-aware transport loop started mosh and fallback SSH with inherited
stderr, so the clients' diagnostics bypassed the launch log. Both now use
the same stderr relay as the dedicated SSH runner. A test subprocess writes
an unterminated diagnostic and checks that it reaches the terminal and log.

### Supervisor lifecycle regression assertion

The quick-failure command now persists both its reason and the agent's exit
status. The lifecycle regression still expected the old reason-only JSON, so
it failed even though the generated command retained the reason correctly.
The assertion now checks the complete command, including the status variable.
It still checks the consecutive-failure reason and that running state is
persisted before publishing the started event. No production change is needed;
this is a contained, reversible test correction with no public API change.

The two reported regressions passed in an isolated archive of `6d68fb1d`
(`2 passed in 1.32s`) and failed at `0dfaa078` (`2 failed in 2.18s`). After
correcting the lifecycle expectation, this command returned `1 passed in 0.26s`:

```sh
uv run --no-sync pytest -n 0 tests/test_session_lifecycle_script.py::test_given_generated_supervisor_when_failed_start_stops_then_unknown_cause_is_written
```

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
