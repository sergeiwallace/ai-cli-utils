---
title: "Exiting a Claude Code session so the name can be resumed"
category: procedure
tags: [procedure, session-launch, process-lifecycle, proc]
status: active
template_version: "procedure-1.0.0"
---

# Exiting a Claude Code session so the name can be resumed

`ai c <n>` resumes a session by name. It refuses to resume a name whose process is still
running, because continuing a transcript that a live process is appending to would silently
merge two conversations. That refusal is correct — but it depends on the process actually being
gone once you exit, and an exit does not guarantee that.

A session process can survive in state `T` (**stopped**): present in `/proc`, holding its pid
and its open files, and never resuming on its own. It is indistinguishable from a session you
are using if all you check is whether the pid exists. See
[the bug record](../bugs/ai-cli-2139-session-exit-leaves-stopped-process.md) for the
measurements.

So: exiting is two steps, not one. Exit, then **verify the process is gone**.

## Procedure

### 1. Exit from inside the session

Use `/exit`, or Ctrl-D at an empty prompt. Let the session close itself so its shutdown work
(transcript flush, worktree state, cleanup traps) runs.

Ctrl-Z does not exit. In an `ai c` session it does not even suspend for long: Claude Code says
"Run `fg` to bring Claude Code back", but an `ai c` pane has no job-control shell, so `fg` and
every other key would go nowhere. The session's watchdog resumes it within about a second
instead. See [Recovering from an accidental Ctrl-Z](#recovering-from-an-accidental-ctrl-z).

### 2. Verify the process is gone — by absence, not by a return code

```bash
pgrep -af 'claude .*--name myproject-3'    # nothing printed = gone
```

If you have the pid (the session registry records it in `~/.claude/sessions/<pid>.json`):

```bash
ls -d /proc/<pid>            # Linux: "No such file or directory" = gone
ps -o pid=,stat= -p <pid>    # portable: no output = gone; a leading T = stopped
```

**Never** treat a terminate's return code as the answer. `kill -TERM <pid>` returns 0 for a
stopped process it does not end: the signal is queued, and a stopped process does not run, so it
never acts on it. On the reported case the kill returned 0 and the process was still there four
seconds later.

### 3. If it is still there, read the state before signalling anything

```bash
ps -o pid=,stat=,args= -p <pid>
```

| State | Meaning | What to do |
|---|---|---|
| `S`, `R`, `D` | running or sleeping — the session is genuinely alive | do not kill it; attach to it instead (`claude agents`) |
| `T` | stopped (job control) or `t` traced | abandoned: end it as below |
| `Z` | already exited, waiting on its parent | nothing to do; it holds nothing open |
| no output | gone | nothing to do |

### 4. End an abandoned process — or just relaunch

The simplest route is to relaunch the session: `ai c <n>` detects a recorded process that is
present but not running and no longer owns its terminal (a Ctrl-Z'd session that still does is
resumed instead, see below), ends it and its process group, prunes the stale registry record, prints
what it found and what it did, and then resumes the session. That is the intended path, and it
is why this normally needs no manual step.

To do it by hand, the escalation is `SIGTERM`, then `SIGCONT`, then a bounded wait, then
`SIGKILL` — and it must target the process **group**, so a wrapper's children are not left
orphaned:

```bash
pgid=$(ps -o pgid= -p "$pid" | tr -d ' ')
kill -TERM -"$pgid"          # queued while the process is stopped
kill -CONT -"$pgid"          # now it can act on the SIGTERM
sleep 2
ls -d /proc/"$pid" 2>/dev/null && kill -KILL -"$pgid"
ls -d /proc/"$pid"           # must print "No such file or directory"
```

The `SIGCONT` is not an extra precaution; it is the step that makes the `SIGTERM` mean anything.
The last line is the only line that proves the exit worked.

Check the process group before signalling it: `kill -- -$pgid` on your *own* shell's process
group would kill your shell along with the target. If `$pgid` equals `ps -o pgid= -p $$`,
signal the pid alone (`kill -TERM "$pid"; kill -CONT "$pid"`).

### 5. Do not hand-delete session records

`~/.claude/sessions/<pid>.json` is pruned automatically once the process it names is provably
gone. Deleting a record whose process is still alive hides a live session from every tool that
reads the registry, which is a worse failure than the one you were fixing.

## Recovering from an accidental Ctrl-Z

In an `ai c` session there is **no key to type**: wait a second and the session comes back by
itself, the same process with every background agent and shell intact. Do not kill it.

If a pane is still frozen after a few seconds (a session started before this watchdog existed,
or one whose watchdog has died), run `ai c <n>` for it from any other terminal. Re-attaching
continues a stopped pane and prints `ai-cli: '<session>' was suspended (Ctrl+Z); resumed its
process group <pgid>.` `ai attach <session>` and `ai c -r` do the same. By hand, continue the
pane's foreground group:

```bash
pane_pid=$(tmux display-message -p -t <session> '#{pane_pid}')
tpgid=$(ps -o tpgid= -p "$pane_pid" | tr -d ' ')
ps -o stat= -p "$tpgid"     # a leading T = stopped
kill -CONT -"$tpgid"
```

A stopped process that still owns its terminal's foreground is a suspended session, not an
abandoned one, so step 4 above never ends it: the launcher resumes it and keeps treating the
name as in use. Bug record: [Ctrl+Z suspends an `ai c` session](../bugs/ai-cli-y6el-ctrl-z-suspends-session-with-no-way-back.md).

## Platform note

The stopped-state detection reads `/proc/<pid>/stat`, so the automatic reclamation in step 4
applies on Linux. macOS and Windows have no `/proc`; there the launcher falls back to pid
existence alone and cannot tell a stopped process from a running one, so step 2's verification
and step 4's manual escalation are the whole procedure. Do the check with
`ps -o pid=,stat= -p <pid>` (macOS) or `Get-Process -Id <pid>` (Windows).

## Related

- [Bug record: exiting a session can leave its process stopped, not dead](../bugs/ai-cli-2139-session-exit-leaves-stopped-process.md)
- [Bug record: Ctrl+Z suspends an `ai c` session and no key resumes it](../bugs/ai-cli-y6el-ctrl-z-suspends-session-with-no-way-back.md)
