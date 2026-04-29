---
title: "Circus-Managed signal-watch"
category: plan
tags: [signal-watch, circus, handoff, process-management]
status: implemented
task: AI-CLI-72
source: session-2026-04-01
---

# Circus-Managed signal-watch

## Table of Contents

- [Background](#background)
- [Problem](#problem)
- [Options](#options)
- [Recommendation](#recommendation)
- [Implementation Plan](#implementation-plan)
  - [1. circus.ini base config](#1-circusini-base-config)
  - [2. _ensure_circusd()](#2-_ensure_circusd)
  - [3. ai signal-watch start](#3-ai-signal-watch-start)
  - [4. ai signal-watch stop](#4-ai-signal-watch-stop)
  - [5. ai signal-watch status](#5-ai-signal-watch-status)
  - [6. Bash template changes](#6-bash-template-changes)
  - [7. Tests](#7-tests)
  - [8. Implementation sequence](#8-implementation-sequence)
- [Approval Log](#approval-log)

## Background

`ai internal signal-watch <project> <session_id>` subscribes to NATS handoff messages for a CC session and writes pending marker files when handoffs arrive. It is currently launched as a background child process inside the `ai c` bash template:

```bash
ai internal signal-watch "$project_name" "$tmux_session" &>/dev/null &
signal_watch_pid=$!
```text

## Problem

Signal-watch is a child process of the CC session's bash wrapper. Running `pkill -f "ai internal signal-watch"` to restart it can match the parent bash process (whose command string contains the string "signal-watch" from the template code), killing all CC sessions on the machine.

## Options

**Option A — `setsid`**: Prepend `setsid` to the launch. Detaches from the process group. Simple one-line change. No process management — no restart on crash, no central control.

**Option B — Circus**: Run signal-watch as a Circus-managed watcher. Full process isolation, `circusctl` control, restart policies, logging. Circus is already a declared dependency (installed, unused).

## Recommendation

**Option B (Circus)**. Circus is already installed. Signal-watch is a long-running daemon and belongs under a process manager. This also enables `ai signal-watch restart <session>` as a safe, intentional operation.

> **Feedback:**
> Option B approved 2026-04-01.

## Implementation Plan

### 1. circus.ini base config

**Location:** `{XDG_STATE_HOME}/ai-cli/circus.ini` — generated at runtime by `_ensure_circusd()`, not shipped as a static file.

```ini
[circus]
endpoint        = ipc:///home/user/.local/state/ai-cli/circus.endpoint
pubsub_endpoint = ipc:///home/user/.local/state/ai-cli/circus.pubsub
logoutput       = /home/user/.local/state/ai-cli/circus.log
umask           = 0o022
```text

- IPC (not TCP) — no port collision, no network exposure
- No `[watcher:*]` sections — all watchers added dynamically via API
- `pubsub_endpoint` required (circusd refuses to start without it)

### 2. `_ensure_circusd()`

```python
def _ensure_circusd() -> str:
    """Start circusd if not already running. Returns the endpoint URI."""
```text

1. Compute `state_dir = get_xdg_state_home()`, ensure it exists
2. Compute `endpoint = f"ipc://{state_dir}/circus.endpoint"`
3. Try `CircusClient(endpoint, timeout=1.0).send_message("status")` — if success, return endpoint
4. On failure: write `circus.ini` to `state_dir / "circus.ini"` (idempotent)
5. Locate `circusd` via `shutil.which("circusd")`, fall back to known uv-tools path
6. Launch: `subprocess.Popen([circusd_bin, "--daemon", "--pidfile", ..., ini_path])` — Popen not run (--daemon forks immediately)
7. Poll endpoint up to 10 × 0.3s retries
8. Return endpoint

**Env note:** `Popen` with `env=None` inherits the caller's env — `AI_CLI_HOST` etc. reach child processes automatically.

### 3. `ai signal-watch start <project> <session>`

```python
def _cmd_signal_watch_start(project: str, session: str) -> None:
```text

1. `endpoint = _ensure_circusd()`
2. `watcher_name = f"sw-{session}"`
3. `cmd = f"{ai_bin} internal signal-watch {project} {session}"`
4. Try `client.send_message("rm", name=watcher_name)` — swallow exception if not found (idempotent)
5. `client.send_message("add", name=watcher_name, cmd=cmd, options={...}, start=True)`

Options:
- `copy_env: True` — inherit `AI_CLI_HOST`, `HOME`, etc.
- `respawn: False` — signal-watch exits on NATS disconnect; don't auto-restart after explicit stop
- `singleton: True` — one process per watcher
- `autostart: True`

### 4. `ai signal-watch stop <session>`

```python
def _cmd_signal_watch_stop(session: str) -> None:
```text

Sends `rm sw-{session}` to circusd. **Never raises** — EXIT trap calls this unconditionally; circusd may not be running.

### 5. `ai signal-watch status`

```python
def _cmd_signal_watch_status() -> None:
```text

Calls `circusd status`, filters to `sw-*` watchers, prints `{session}: {status}`.

### 6. Bash template changes

**Line ~943 — replace direct launch:**

```bash
# OLD:
ai internal signal-watch "$project_name" "$tmux_session" &>/dev/null &
signal_watch_pid=$!

# NEW:
ai signal-watch start "$project_name" "$tmux_session" &>/dev/null
signal_watch_pid=""
```text

**EXIT trap — replace `kill "$signal_watch_pid"`:**

```bash
# OLD:
kill "$signal_watch_pid" 2>/dev/null

# NEW:
ai signal-watch stop "$tmux_session" &>/dev/null
```text

### 7. Tests

New `TestSignalWatchCircus` class in `tests/test_main.py`:

| Test | What it verifies |
|------|-----------------|
| `test_ensure_circusd_when_already_running_then_no_popen` | No Popen if circusd reachable |
| `test_ensure_circusd_when_not_running_then_starts_daemon_and_writes_ini` | Writes ini + starts daemon |
| `test_cmd_signal_watch_start_registers_watcher_with_copy_env` | `add` called with `copy_env=True, start=True` |
| `test_cmd_signal_watch_start_idempotent_on_second_call` | `rm` exception swallowed |
| `test_cmd_signal_watch_stop_when_circusd_running` | `rm` sent with `name="sw-c-sw-1"` |
| `test_cmd_signal_watch_stop_when_circusd_not_running_then_silent` | ZMQError swallowed, exits 0 |
| `test_cmd_signal_watch_status_filters_sw_prefix` | Only `sw-*` watchers printed |
| `test_cli_signal_watch_start_dispatches` | CLI routes to `_cmd_signal_watch_start` |
| `test_cli_signal_watch_stop_dispatches` | CLI routes to `_cmd_signal_watch_stop` |
| `test_cli_signal_watch_missing_args_exits_1` | Missing args → exit 1 |

**Mock strategy:** `circus.client.CircusClient` is imported inside function bodies. Patch at `circus.client.CircusClient`.

### 8. Implementation sequence

1. Add `_ensure_circusd()` above `cli()`
2. Add `_cmd_signal_watch_start/stop/status()` below it
3. Add `signal-watch` dispatch block in `cli()` (between `sync` and `reconnect` blocks)
4. Edit bash template: line ~943 (start) and EXIT trap (stop)
5. Add `TestSignalWatchCircus` to `tests/test_main.py`
6. Run checks: `ruff check + ruff format --check + pytest`
7. Commit, push, deploy to Hetzner, instruct user to deploy on Mac

## Approval Log

- 2026-04-01, Round 1: Option B (Circus) approved. Proceed to implementation.
