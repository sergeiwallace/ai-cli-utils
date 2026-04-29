---
title: "Resilient SSH Tunnels via autossh"
category: plan
tags: [tunnel, autossh, ssh, remote]
status: implemented
task: AI-CLI-73
source: session-2026-04-01
---

# Resilient SSH Tunnels via autossh

## Table of Contents

- [Background](#background)
- [Problem](#problem)
- [Options](#options)
  - [Option A: ssh with manual reconnect loop](#option-a-ssh-with-manual-reconnect-loop)
  - [Option B: autossh](#option-b-autossh)
  - [Option C: systemd/launchd user service](#option-c-systemdlaunchd-user-service)
- [Recommendation](#recommendation)
- [Implementation Plan](#implementation-plan)
  - [1. _cmd_tunnel_start()](#1-_cmd_tunnel_start)
  - [2. _cmd_tunnel_stop()](#2-_cmd_tunnel_stop)
  - [3. _cmd_tunnel_status()](#3-_cmd_tunnel_status)
  - [4. CLI dispatch block](#4-cli-dispatch-block)
  - [5. Tests](#5-tests)
  - [6. Checks](#6-checks)
  - [7. Ship](#7-ship)
- [Approval Log](#approval-log)

## Background

The user runs `ssh -R 9222:localhost:9222 user@192.0.2.1` to forward a local Chrome debug port to the Hetzner server, enabling remote browser automation. This is a recurring need whenever CC sessions on Hetzner need CDP access to a local Chrome instance.

## Problem

Plain SSH tunnels die from broken pipe, NAT timeout, and network interruptions. The user must manually detect the failure and reconnect. There is no visibility into whether a tunnel is currently alive.

## Options

### Option A: ssh with manual reconnect loop

A shell script wrapper that runs `ssh -R ...` in a `while true` loop with a sleep between retries.

**Pros:**
- No additional dependencies
- Simple to understand

**Cons:**
- No health monitoring beyond "did the process exit"
- Reconnect delay is a fixed sleep, not adaptive
- PID management and cleanup are manual
- Fragile — edge cases around signal handling, partial failures

### Option B: autossh

`autossh` restarts SSH sessions automatically. With `-M 0` and SSH keepalives (`ServerAliveInterval`, `ServerAliveCountMax`), it detects dead connections via the SSH protocol itself and reconnects.

**Pros:**
- Purpose-built for persistent SSH tunnels
- SSH keepalives detect dead connections within ~90 seconds
- `ExitOnForwardFailure=yes` prevents silent port-bind failures
- Available on both macOS (`brew install autossh`) and Linux (`apt install autossh`)
- Single background process, simple PID management

**Cons:**
- Requires `autossh` to be installed (not bundled with OS)

### Option C: systemd/launchd user service

Define the tunnel as a user-level systemd service (Linux) or launchd plist (macOS).

**Pros:**
- Full process lifecycle management by the OS
- Automatic restart on boot
- Logging via journald/syslog

**Cons:**
- Platform-specific: requires separate systemd unit and launchd plist
- More complex setup and teardown
- Overkill for ad-hoc tunnels that only run during work sessions
- User must manage service files outside ai-cli

## Recommendation

**Option B (autossh)**. It is the standard tool for persistent SSH tunnels, handles reconnection automatically via SSH keepalives, and works identically on macOS and Linux. No system service management is needed — ai-cli manages the process directly via PID files.

> **Feedback:**
> Option B approved 2026-04-01.

## Implementation Plan

### 1. `_cmd_tunnel_start()`

```python
def _cmd_tunnel_start(local_port: int, remote_port: int, *, forward: bool = False) -> None:
```text

1. Check `shutil.which("autossh")` — if not found, print install instructions (`brew install autossh` / `apt install autossh`) and `sys.exit(1)`
2. Read `host` and `user` from config `[remote]` section
3. Build direction flag: `-L` if `forward=True`, otherwise `-R`
4. Build command:
   ```text
   autossh -M 0
     -o ServerAliveInterval=30
     -o ServerAliveCountMax=3
     -o ExitOnForwardFailure=yes
     -N
     {direction_flag} {remote_port}:localhost:{local_port}
     {user}@{host}
   ```text
5. Launch via `subprocess.Popen(cmd)`, detached from terminal
6. Write PID to `{state_dir}/tunnel-{local_port}.pid`
7. Print confirmation: `Tunnel started: localhost:{local_port} -> {host}:{remote_port} (PID {pid})`

### 2. `_cmd_tunnel_stop()`

```python
def _cmd_tunnel_stop(local_port: int) -> None:
```text

1. Read PID from `{state_dir}/tunnel-{local_port}.pid`
2. If PID file does not exist, exit silently (exit 0)
3. Send `SIGTERM` to the process. Swallow `ProcessLookupError` (already dead).
4. Remove PID file
5. Print confirmation: `Tunnel stopped: port {local_port}`

### 3. `_cmd_tunnel_status()`

```python
def _cmd_tunnel_status() -> None:
```text

1. Glob `{state_dir}/tunnel-*.pid`
2. For each PID file: read PID, check if process is alive (`os.kill(pid, 0)`)
3. Print table: `port | PID | status (alive/dead)`
4. Clean up stale PID files (dead processes)

### 4. CLI dispatch block

```bash
ai tunnel start <local-port> [remote-port] [--forward]
ai tunnel stop <port>
ai tunnel status
```text

- `remote-port` defaults to `local-port` if omitted
- `--forward` flag switches from `-R` (reverse) to `-L` (forward)
- Add dispatch in `cli()` between existing command blocks

### 5. Tests

New `TestTunnel` class in `tests/test_main.py`:

| Test | What it verifies |
|------|-----------------|
| `test_cmd_tunnel_start_when_autossh_found_then_launches_reverse_tunnel` | Popen called with `-R`, PID file written |
| `test_cmd_tunnel_start_when_forward_flag_then_uses_dash_L` | Popen args contain `-L` instead of `-R` |
| `test_cmd_tunnel_start_when_remote_port_omitted_then_defaults_to_local_port` | Both ports equal in the command |
| `test_cmd_tunnel_start_when_autossh_missing_then_exits_1` | `shutil.which` returns None, sys.exit(1) |
| `test_cmd_tunnel_stop_when_pid_file_exists_then_kills_and_removes` | SIGTERM sent, PID file deleted |
| `test_cmd_tunnel_stop_when_no_pid_file_then_silent` | No error, exits 0 |
| `test_cmd_tunnel_status_lists_active_tunnels` | Output includes port and PID for alive processes |
| `test_cli_tunnel_start_dispatches` | CLI argv routes to `_cmd_tunnel_start` |
| `test_cli_tunnel_missing_args_exits_1` | `ai tunnel start` with no port exits 1 |

**Mock strategy:** Patch `shutil.which`, `subprocess.Popen`, `os.kill`. Use `tmp_path` for state dir.

**Config used:**

```toml
[remote]
host = "192.0.2.1"
user = "user"
```text

### 6. Checks

Run `ruff check src/ tests/ && ruff format --check src/ tests/ && pytest`.

### 7. Ship

Commit, push, deploy to Hetzner and Mac (3-place install per `feedback_aicli_deploy.md`).

## Approval Log

- 2026-04-01, Round 1: Approved. Proceed to implementation.
