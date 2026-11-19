---
title: "Launch Extensions"
category: guide
tags: [launch, extensions, config, hooks]
status: current
source: internal
---

# Launch Extensions

A launch extension is a command of your own that `ai c` runs before it starts a session. Use one to start a helper process or to check that something the session needs is in place. Extensions live outside this package: config.toml names the command and the directory it runs in, so the code can sit in any repository you choose, and a config file you share between machines carries no machine-specific paths.

With no extensions configured, a launch does exactly what it did before: nothing runs and nothing extra is printed.

## Table of Contents

- [Configuration](#configuration)
- [Where an extension runs](#where-an-extension-runs)
- [When an extension runs](#when-an-extension-runs)
- [Errors: warn or fail](#errors-warn-or-fail)
- [What is logged](#what-is-logged)
- [Example: a heartbeat file](#example-a-heartbeat-file)
- [Windows](#windows)

## Configuration

Extensions are configured in `config.toml` (see [Tool Config](../../README.md#tool-config) for where it lives). The `[extensions]` table holds the defaults for every extension, and each extension is a table of its own under `[extensions.run]`:

```toml
[extensions]
on_error = "fail"          # default for every extension: "fail" or "warn"
timeout_seconds = 30       # default time limit for each extension

[extensions.run.heartbeat]
path = "myextensions"                                                    # where it runs
command = ["python3", "scripts/heartbeat.py", "--file", "heartbeat.txt"] # what it runs
on_error = "warn"          # optional: overrides the default for this extension only
timeout_seconds = 10       # optional: overrides the default for this extension only
```

| Key | Table | Default | Meaning |
|-----|-------|---------|---------|
| `on_error` | `[extensions]` | `"fail"` | What a failing extension does, unless it sets its own |
| `timeout_seconds` | `[extensions]` | `30` | Seconds each extension may run, unless it sets its own |
| `path` | `[extensions.run.<name>]` | `projects_dir` itself | The directory the command runs in |
| `command` | `[extensions.run.<name>]` | required | The command and its arguments, as a list of strings |
| `on_error` | `[extensions.run.<name>]` | the `[extensions]` value | This extension's error policy |
| `timeout_seconds` | `[extensions.run.<name>]` | the `[extensions]` value | This extension's time limit |

The `<name>` of each table is how the extension is named in launch output and the log. Extensions run in the order they appear in the file.

The config is read on every launch, so an edit applies to the next `ai c` without reinstalling anything. A config that does not validate (an unknown `on_error` value, a `command` that is not a list of strings, a timeout that is not a positive number) refuses the launch with an error naming the key, whatever the policy says: a broken extension setup is never silently skipped.

## Where an extension runs

`path` may be absolute or relative:

- An absolute path, such as `/opt/myextensions`, is used as written. `~` expands to your home directory.
- A relative path, such as `myextensions`, resolves against `[project] projects_dir` (default `~/projects`). This is the form to use in a config shared between machines: each machine resolves it under its own projects directory.

The command runs with that directory as its working directory, so a relative argument such as `scripts/heartbeat.py` is read from inside it.

The first element of `command` names the program:

- A bare name such as `python3` is looked up on `PATH`.
- A relative path with a directory part, such as `bin/start`, resolves inside `path`. It must be executable.
- An absolute path is used as written.

The command runs directly, never through a shell: no globbing, pipes or variable expansion. Write a script if you need them.

## When an extension runs

Each extension runs once per `ai c` launch, after the launcher's own update check and before the session starts. For a remote launch (`ai c -R`) it runs on the remote machine, the one that hosts the session, not on the machine you typed the command on. A dry run (`--dry-run`) runs no extensions.

An extension runs to completion: the launch waits until it exits or its `timeout_seconds` passes, and a command still running at the timeout is stopped and counts as failed. An extension that needs something to keep running after the launch, such as a watcher or a local server, must start that process detached (in the background, with its own output redirected) and then exit. The launcher does not track or stop processes an extension leaves behind.

Its standard input is empty, so it cannot prompt for input. Its standard output and error are captured into the launch log, and the last non-empty line it printed is also shown on the terminal, so an extension can report its own one-line status there (for example "started" or "disabled on this machine").

## Errors: warn or fail

An extension fails when:

- its `path` directory does not exist;
- its command cannot be found, or is not executable;
- it exits with a non-zero status;
- it is still running at its timeout.

What happens next is the extension's `on_error` policy, or the `[extensions]` default when it sets none:

| Policy | Terminal and launch log | The session |
|--------|-------------------------|-------------|
| `"warn"` | A `Warning:` line naming the extension and the reason | Starts as usual |
| `"fail"` | An `Error:` line naming the extension and the reason | Does not start; `ai c` exits with status 1 |

The shipped default is `"fail"`. An extension you configured is one you expect to run, so a failure stops the launch where you can see it rather than leaving a session running without it. Set `on_error = "warn"` on an extension whose failure should not stop your work.

A typical line reads:

```text
[launch] extension: Error: heartbeat: exited with status 2 in 0.1s; output in /home/user/.local/state/ai-cli-utils/launch-logs/session-1-20260101T120000.000000Z-4242.log; launch stopped (on_error = "fail")
```

## What is logged

Every launch writes a timestamped log under the launcher's state directory, in `launch-logs/` (on Linux and macOS `~/.local/state/ai-cli-utils/launch-logs/`, on Windows the ai-cli-utils folder under `%LOCALAPPDATA%`). Its name is the session name, a UTC timestamp and the process id, for example `session-1-20260101T120000.000000Z-4242.log`, and only the most recent logs per session are kept. For each extension that log holds:

- the start: the resolved command, its directory, its timeout and its policy;
- every line the extension printed, on standard output or standard error;
- the outcome: its exit status and how long it ran, or why it could not run;
- the warning or error, if it failed.

On the terminal you see one line per extension: its exit status when it succeeds, or the warning or error when it fails, followed by the last line the extension printed. Run `ai c` with `-v/--verbose` to see the start line and every output line there too.

## Example: a heartbeat file

This extension records the time of every launch in a file. Put the script in a repository of your own under your projects directory, here `~/projects/myextensions/scripts/heartbeat.py`:

```python
"""Append the current UTC time to a heartbeat file."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--file", required=True)
args = parser.parse_args()

with Path(args.file).open("a", encoding="utf-8") as heartbeat:
    heartbeat.write(f"{datetime.now(UTC).isoformat()}\n")
print(f"heartbeat written to {args.file}")
```

Then name it in `config.toml`:

```toml
[extensions.run.heartbeat]
path = "myextensions"
command = ["python3", "scripts/heartbeat.py", "--file", "heartbeat.txt"]
on_error = "warn"
```

Launch a session with `ai c 1`. The launch prints a line like `[launch] extension: heartbeat: exited with status 0 in 0.1s`, `~/projects/myextensions/heartbeat.txt` gains a line, and the launch log holds the script's `heartbeat written to heartbeat.txt`.

To see the policies at work, change the script's last line to `raise SystemExit(2)`. With `on_error = "warn"` the launch prints a warning and the session still starts; remove that line, so the `"fail"` default applies, and the launch stops with an error before the session starts.

## Windows

Extensions run on Windows (including Git Bash) the same way, with two differences to keep in mind:

- Python is often installed as `python` or `py`, not `python3`. Use the name that works in your shell, or an absolute path to the interpreter.
- A script is not executable by itself on Windows. Name its interpreter as the first element of `command` (`["python", "scripts/heartbeat.py"]`, `["bash", "scripts/start.sh"]`) rather than the script alone. A `.exe`, `.bat` or `.cmd` file can be named directly; `PATHEXT` is honoured, so `bin/start` finds `bin/start.cmd`.

Write `path` and relative commands with forward slashes; they work on every platform.
