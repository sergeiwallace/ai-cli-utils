---
title: VPN-Aware Transport Switching
category: designs
tags: [mosh, ssh, vpn, transport, session-management]
status: draft
source: internal
---

# VPN-Aware Transport Switching

**Status:** DRAFT

**Created:** 2026-04-06

## Table of Contents

- [Problem Statement](#problem-statement)
- [Current Architecture](#current-architecture)
- [Design Decisions](#design-decisions)
- [Switching Mechanism](#switching-mechanism)
- [Session and Process State Tracking](#session-and-process-state-tracking)
- [iTerm2 Pane Continuity](#iterm2-pane-continuity)
- [Edge Cases](#edge-cases)
- [Implementation Plan](#implementation-plan)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Problem Statement

When Mullvad VPN activates, it blocks UDP traffic. Mosh relies on UDP, so all active mosh connections die and cannot reconnect. The user must manually kill the dead mosh session and re-establish via SSH. When the VPN deactivates, the user is stuck on SSH (higher latency, no roaming) until they manually switch back to mosh.

Goal: automatically switch running remote sessions between mosh and SSH based on VPN state, with no user intervention beyond seeing a brief reconnect message. The remote tmux session on Hetzner must never be killed -- only the local transport process changes.

---

## Current Architecture

Understanding the current code is critical to the design. Here is how remote sessions work today.

### The `ai c -R` Transport Layer (main.py ~2926-3014)

When the user runs `ai c N -R`, the Python CLI process:

1. Reads `[remote]` config (host, user, port, identity_file, transport)
2. Builds both `ssh_args` and `mosh_args` unconditionally
3. Emits iTerm2 profile/color escape sequences (the only opportunity -- mosh blocks them)
4. Enters a **blocking** `subprocess.run()` call on either mosh or SSH

The Python process is the **direct parent** of the transport process. There is **no local tmux session** for remote sessions -- the iTerm2 pane directly hosts the `ai c -R` Python process, which in turn has the mosh-client or ssh child process.

### Current Fallback Logic

The code already handles VPN-at-launch:

- If `_is_vpn_active()` at startup: uses SSH, then tries mosh after SSH exits if VPN is now off
- If mosh fails fast (<10s): falls back to SSH with the same post-exit mosh retry

But this is **launch-time only**. If VPN activates while a mosh session is running, the mosh client hangs/dies and the user is stuck.

### VPN Detection (`_is_vpn_active()`, line 888)

Checks Mullvad CLI (`mullvad status`) first, falls back to scanning `ifconfig` for active utun/tun interfaces with inet addresses. Returns False on any error (fail-open for mosh).

### Signal-Watch / Circus Infrastructure

Each CC session has a Circus-managed `signal-watch` watcher (`sw-{session}`) that subscribes to NATS for handoff delivery. Circus provides:

- IPC endpoint at `~/.local/state/ai-cli-utils/circus.endpoint`
- Dynamic watcher add/remove via `CircusClient`
- Auto-restart capability (currently `respawn=False` for signal-watch)
- Process supervision without systemd

### Process Hierarchy (Remote Session)

```
iTerm2 pane
  +-- ai c N -R  (Python process, PID A)
        +-- mosh-client user@host  (PID B, blocks Python via subprocess.run)
              +-- [mosh protocol over UDP to remote mosh-server]
                    +-- tmux session c-r-prefix-N on Hetzner
                          +-- bash -> claude
```

When mosh dies, `subprocess.run()` returns to the Python process. This is the key leverage point.

---

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Where to put the VPN watcher | (a) Circus watcher, (b) Inline in transport loop, (c) Standalone daemon | (b) Inline | Zero new infrastructure, leverages existing subprocess.run return | Pending |
| 2 | How to kill the active transport | (a) External SIGTERM from watcher, (b) Transport loop self-manages | (a) External SIGTERM + (b) loop handles reconnect | External signal is the only way to interrupt a blocking subprocess.run | Pending |
| 3 | How to track transport state | (a) PID files in XDG state dir, (b) In-memory only | (a) PID files | External watcher needs to find the right process to signal | Pending |
| 4 | VPN watcher lifecycle | (a) One global watcher, (b) Per-session watcher | (a) One global watcher | VPN state is machine-wide, not per-session | Pending |

### Decision Details

#### Decision 1: Where to Put the VPN Watcher

##### (a) Circus Watcher

A new Circus-managed process that polls `_is_vpn_active()` and signals transport processes on state change.

**Pros:**
- Supervised by Circus (auto-restart on crash)
- Fits existing infrastructure pattern (signal-watch uses Circus)
- Decoupled from transport loop

**Cons:**
- Adds a new daemon that runs even when no remote sessions exist
- Circus dependency is heavy for a simple polling loop
- Requires IPC between the watcher and transport processes (PID files or NATS messages)

##### (b) Inline in the Transport Loop

Restructure the `ai c -R` transport code from a single `subprocess.run()` to a **transport loop** that monitors VPN state and re-launches the appropriate transport.

**Pros:**
- No new daemons or processes
- Transport loop has direct access to all session context (ssh_args, mosh_args, session name)
- Natural error handling -- subprocess.run returning means "time to decide what to do next"
- Simplest implementation

**Cons:**
- Requires an external signal to interrupt the blocking subprocess.run (the Python process cannot poll while blocked)
- Still needs a separate "VPN watcher" thread or process to deliver that signal

##### (c) Standalone Daemon

A separate long-running process outside Circus.

**Pros:**
- Full control over lifecycle

**Cons:**
- Yet another daemon to manage
- No supervision (Circus, launchd, etc.)
- Duplicates infrastructure already available

##### Recommendation

**Hybrid of (a) and (b):** A single global Circus watcher polls VPN state. When state changes, it signals all active transport processes via SIGTERM. The transport loop in `ai c -R` (option b) catches the signal and reconnects with the appropriate transport. This gives us Circus supervision for the watcher and clean reconnect logic in the transport loop.

However, there is a simpler alternative worth considering: **(b) alone with a background thread**. The Python `ai c -R` process spawns a lightweight daemon thread that polls `_is_vpn_active()` every N seconds. On state change, the thread sends SIGTERM to the child transport process (the mosh-client or ssh PID from `subprocess.Popen`). The main thread's transport loop handles the rest. This avoids Circus entirely for the watcher and keeps everything self-contained.

**Final recommendation: (b) with a background thread** for the initial implementation. The thread is trivial, dies when the process exits, and requires no external state. If multiple remote sessions are common and the overhead of N polling threads (one per session) becomes a concern, upgrade to a single Circus watcher (a) in a later phase.

---

#### Decision 2: How to Kill the Active Transport

The core problem: `subprocess.run(mosh_args)` is a **blocking call**. The Python process cannot check VPN state while mosh-client is running. Something external must interrupt it.

##### (a) Send SIGTERM to the Child Transport Process

The VPN watcher thread (or Circus process) finds the child mosh-client/ssh PID and sends SIGTERM. `subprocess.run()` returns with a non-zero exit code. The transport loop inspects VPN state and reconnects.

**Pros:**
- Clean -- mosh-client handles SIGTERM gracefully (closes connection)
- `subprocess.run()` returns naturally
- No signal handler complexity in the parent Python process

**Cons:**
- Need to track the child PID (requires switching from `subprocess.run` to `subprocess.Popen`)

##### (b) Send SIGTERM/SIGUSR1 to the Parent Python Process

Install a signal handler in the Python process that kills the child and sets a flag.

**Pros:**
- External watcher only needs to know the parent PID (simpler tracking)

**Cons:**
- Signal handlers in Python are limited (only main thread, restricted to async-signal-safe operations)
- More complex, harder to test

##### Recommendation

**(a) Send SIGTERM to the child.** Switch from `subprocess.run()` to `subprocess.Popen()` + `proc.wait()` so we have the child PID available. The background thread stores a reference to the `Popen` object and calls `proc.terminate()` on VPN state change.

---

#### Decision 3: How to Track Transport State

##### (a) PID Files

Write `{state_dir}/transport-{session}.json` containing:
```json
{
  "parent_pid": 12345,
  "child_pid": 12346,
  "transport": "mosh",
  "session": "c-r-sw-1",
  "host": "1.2.3.4",
  "started": "2026-04-06T10:00:00Z"
}
```

**Pros:**
- External tools can inspect transport state (`ai ps` enhancement)
- Survives if we later move to a Circus watcher model
- Useful for `ai reconnect` to show current transport type

**Cons:**
- Must clean up on exit (add to EXIT trap / atexit)

##### (b) In-Memory Only

The background thread and transport loop share state via a threading.Event or similar.

**Pros:**
- Simplest, no file I/O

**Cons:**
- No external visibility
- Cannot upgrade to multi-process watcher without rewriting

##### Recommendation

**(a) PID files.** The overhead is negligible, and external visibility (for `ai ps`, debugging, and potential Circus upgrade) is worth it. Write on transport start, delete on exit via `atexit`.

---

#### Decision 4: VPN Watcher Lifecycle

##### (a) One Global Watcher

A single Circus-managed process watches VPN state and signals all registered transport processes.

**Pros:**
- Single poll loop regardless of session count
- Centralized state tracking

**Cons:**
- Requires a registry of active transport processes
- More infrastructure

##### (b) Per-Session Watcher Thread

Each `ai c -R` process runs its own background thread.

**Pros:**
- Zero coordination -- each process is self-contained
- Thread dies automatically when the Python process exits
- Direct access to the Popen object (no IPC needed)

**Cons:**
- N sessions = N independent VPN poll loops (negligible cost -- `mullvad status` is ~2ms)

##### Recommendation

**(b) Per-session watcher thread** for now. The per-thread overhead is negligible for typical session counts (1-5 remote sessions). VPN state polling is cheap (2ms per check, every 3-5 seconds = ~0.1% CPU total across all sessions).

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. Decision 1 (background thread vs Circus watcher):
> 2. Decision 2 (SIGTERM to child):
> 3. Decision 3 (PID files):
> 4. Decision 4 (per-session thread):
> - <enter feedback here>

---

## Switching Mechanism

### Transport Loop (Replaces Current Linear Logic)

The current code at lines 2982-3010 is a linear if/else that runs one transport and optionally falls back. The new design replaces this with a **transport loop**:

```python
def _run_transport_loop(
    ssh_args: list[str],
    mosh_args: list[str],
    cleanup_cmd: list[str],
    session_name: str,
) -> None:
    """Run the transport loop, switching between mosh and SSH based on VPN state.
    
    The loop runs until the user intentionally exits (clean SSH/mosh exit after
    a non-trivial duration) or an unrecoverable error occurs.
    """
    state_dir = get_xdg_state_home()
    transport_file = state_dir / f"transport-{session_name}.json"
    
    # Shared state between main thread and VPN watcher thread
    current_proc: subprocess.Popen | None = None
    proc_lock = threading.Lock()
    vpn_changed = threading.Event()
    shutdown = threading.Event()
    
    def _vpn_watcher():
        """Background thread: polls VPN state, signals transport on change."""
        last_vpn = _is_vpn_active()
        while not shutdown.is_set():
            shutdown.wait(3.0)  # Poll every 3 seconds
            if shutdown.is_set():
                break
            now_vpn = _is_vpn_active()
            if now_vpn != last_vpn:
                last_vpn = now_vpn
                vpn_changed.set()
                with proc_lock:
                    if current_proc and current_proc.poll() is None:
                        current_proc.terminate()
    
    watcher = threading.Thread(target=_vpn_watcher, daemon=True)
    watcher.start()
    
    try:
        while True:
            vpn_active = _is_vpn_active()
            vpn_changed.clear()
            
            if vpn_active:
                print("VPN active -- connecting via SSH...", file=sys.stderr)
                args = ssh_args
                transport_type = "ssh"
            else:
                print("No VPN -- connecting via mosh...", file=sys.stderr)
                args = mosh_args
                transport_type = "mosh"
            
            with proc_lock:
                current_proc = subprocess.Popen(args)
            
            # Write transport state file
            _write_transport_state(transport_file, session_name, 
                                   current_proc.pid, transport_type)
            
            start_time = time.monotonic()
            current_proc.wait()
            elapsed = time.monotonic() - start_time
            
            with proc_lock:
                current_proc = None
            
            # Decide what to do next
            if vpn_changed.is_set():
                # VPN state changed -- loop back to reconnect with correct transport
                print("\nVPN state changed -- switching transport...", file=sys.stderr)
                continue
            
            if transport_type == "mosh" and elapsed < 10:
                # Mosh failed fast (possibly VPN activated during connection)
                if _is_vpn_active():
                    print(f"\nmosh failed ({elapsed:.1f}s), VPN detected -- "
                          f"switching to SSH...", file=sys.stderr)
                    continue
                # Mosh failed for another reason -- don't retry endlessly
                print(f"\nmosh failed ({elapsed:.1f}s) -- retrying once...", 
                      file=sys.stderr)
                time.sleep(1)
                continue
            
            if elapsed < 3:
                # Transport died almost immediately -- unrecoverable
                print(f"\nTransport died too quickly ({elapsed:.1f}s) -- "
                      f"giving up.", file=sys.stderr)
                break
            
            # Normal exit (user detached or session ended)
            break
    finally:
        shutdown.set()
        transport_file.unlink(missing_ok=True)
        subprocess.run(cleanup_cmd, capture_output=True)
```

### VPN Watcher Thread Detail

The watcher thread is intentionally minimal:

1. Polls `_is_vpn_active()` every 3 seconds (configurable via `[remote] vpn_poll_interval`)
2. On state change: sets `vpn_changed` event and calls `proc.terminate()` on the child
3. Thread is `daemon=True` -- dies automatically if the parent exits abnormally
4. Uses `shutdown.wait(N)` instead of `time.sleep(N)` for clean exit

The 3-second interval is a balance: fast enough that the user sees a switch within ~5 seconds of VPN toggling, slow enough that `mullvad status` calls are negligible.

### State Transitions

```
                    +------------------+
                    |   Launch ai c -R |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Check VPN state  |
                    +--------+---------+
                             |
                +------------+------------+
                |                         |
        +-------v-------+       +--------v--------+
        |  Start mosh   |       |   Start SSH     |
        +-------+-------+       +--------+--------+
                |                         |
         [running]                 [running]
                |                         |
        +-------v-------------------------v--------+
        |         VPN watcher detects change        |
        |  -> proc.terminate() -> vpn_changed.set() |
        +------------------+-----------------------+
                           |
                  +--------v---------+
                  | subprocess.wait()|
                  | returns          |
                  +--------+---------+
                           |
                  +--------v---------+
                  | Check VPN state  |
                  | -> loop back     |
                  +------------------+
```

---

## Session and Process State Tracking

### Transport State File

Location: `~/.local/state/ai-cli-utils/transport-{session_name}.json`

```json
{
  "parent_pid": 12345,
  "child_pid": 12346,
  "transport": "mosh",
  "session": "c-r-sw-1",
  "host": "178.104.70.139",
  "started_at": "2026-04-06T10:00:00Z"
}
```

Written when a transport process starts, deleted on clean exit (in `finally` block). Stale files (where `parent_pid` is dead) are cleaned up by `ai ps cron`.

### Integration with `ai ps`

The `ai ps` command can read transport state files to show:
```
c-r-sw-1  mosh  178.104.70.139  pid=12346  2h uptime
c-r-sw-2  ssh   178.104.70.139  pid=12350  5m uptime (VPN active)
```

### Integration with `ai reconnect`

`ai reconnect` currently lists remote tmux sessions and prints `ai c N -R` commands. It can additionally report which sessions are currently connected locally and via which transport, by reading transport state files.

---

## iTerm2 Pane Continuity

This is the simplest aspect of the design because **the iTerm2 pane never changes**. Here is why:

1. The `ai c -R` Python process owns the iTerm2 pane for the entire session lifetime
2. `subprocess.Popen(mosh_args)` / `subprocess.Popen(ssh_args)` runs as a child process **in the same terminal**
3. When the child is terminated and a new one spawns, it inherits the same PTY
4. The user sees the old transport's output scroll up, a "switching transport..." message from the Python process, and the new transport's output begin

**No pane re-attachment is needed.** The Python process never exits during a transport switch -- it just kills one child and spawns another. The iTerm2 pane is bound to the Python process's PTY, not to the mosh/ssh child's PID.

The only visual consideration: re-emit iTerm2 escape sequences after switching from SSH back to mosh (since mosh blocks iTerm2 custom sequences from the remote side). The `_emit_iterm2_profile_setup()` call before the initial launch already handles this; we add the same call at the top of the transport loop before each `Popen()`.

---

## Edge Cases

### VPN Flap (Rapid On/Off/On)

If VPN toggles rapidly (e.g., within 5 seconds):

- The watcher detects the first transition and terminates the transport
- The transport loop checks VPN state *at reconnect time* (not at signal time)
- If VPN has already toggled back, the loop picks the now-correct transport
- Net effect: one unnecessary reconnect, which is acceptable

**Debounce consideration:** Adding a 2-second debounce to the watcher (wait 2s after detecting a change, re-check before signaling) would avoid unnecessary reconnects during flaps. Worth adding if flap frequency is annoying in practice. Not in initial implementation -- keep it simple.

### Multiple Concurrent Remote Sessions

Each `ai c N -R` process runs its own watcher thread. When VPN changes:

- All watcher threads detect the change within ~3 seconds of each other
- Each terminates its own child process independently
- Each transport loop reconnects independently
- No coordination needed -- the reconnects are idempotent

Visible effect: user sees 2-5 sessions print "switching transport..." within a few seconds of each other. Each reconnects independently.

### SSH Fails During VPN (SSH Also Blocked)

Some VPN configurations might block all outbound traffic briefly during connection setup. If SSH fails:

- The transport loop sees `elapsed < 3` and breaks (current behavior)
- **Enhancement:** Instead of breaking, retry with exponential backoff (1s, 2s, 4s) up to 3 times before giving up
- Print clear status: "SSH connection failed, retrying in 2s..."

### Mosh Client Already Dead (Timeout/Network Change)

If mosh-client has already exited (e.g., UDP blocked for >60s and mosh times out):

- `subprocess.wait()` returns immediately (or already returned)
- `proc.terminate()` on an already-dead process raises no error (`terminate()` on a finished Popen is a no-op)
- The transport loop proceeds normally to check VPN state and reconnect

### Transport Switch During Active CC Prompt

The user might be mid-sentence in Claude Code when the switch happens. The remote tmux session preserves all state (CC's TUI is running in the remote tmux). After reconnecting:

- SSH: user is re-attached to the same tmux session, CC TUI redraws, mid-prompt text is preserved
- Mosh: same behavior, mosh's own terminal state sync handles the redraw

There may be a brief flash as the terminal redraws. This is inherent to transport switching and not something we can prevent.

### Python Process Killed (SIGKILL, OOM)

- Transport state file becomes stale (parent PID dead)
- Child mosh/ssh process becomes orphaned but eventually exits (mosh times out, SSH session times out on remote)
- Remote tmux session stays alive -- `ai reconnect` or `ai c N -R` reconnects
- Stale transport file cleaned by `ai ps cron`

### VPN Activates During Mosh Connection Setup

Mosh has a two-phase connection: SSH handshake (to set up the UDP session), then UDP. If VPN activates during the SSH handshake phase:

- SSH handshake may complete (TCP is still working during Mullvad's brief connection window)
- UDP phase will fail -- mosh-client exits fast
- Transport loop catches the fast exit, detects VPN, switches to SSH

If VPN blocks even the initial SSH handshake:

- Mosh exits with an error
- Same fast-fail path, same recovery

---

## Implementation Plan

### Phase 1: Transport Loop Refactor (Core)

**Files:** `src/ai_cli/main.py`

**Changes:**

1. **New function `_run_transport_loop()`** (~80 lines): Implements the loop described in [Switching Mechanism](#switching-mechanism). Replaces the current linear transport logic at lines 2982-3010.

2. **New function `_write_transport_state()`** (~15 lines): Writes the transport state JSON file.

3. **New function `_vpn_watcher_thread()`** (~20 lines): The background thread target. Extracted as a module-level function for testability (can be tested by mocking `_is_vpn_active` and checking that it calls `proc.terminate()` on state change).

4. **Refactor lines 2982-3014**: Replace the if/else block with a single call to `_run_transport_loop(ssh_args, mosh_args, cleanup_cmd, session_name)`. The `subprocess.run` calls become `subprocess.Popen` + `.wait()` inside the loop.

5. **Add `import threading`** to the imports.

6. **Config key**: `[remote] vpn_poll_interval` (default 3, in seconds). Add to `DEFAULT_CONFIG` with a comment.

**Estimated scope:** ~120 new lines, ~30 lines removed.

### Phase 2: State File Integration

**Files:** `src/ai_cli/main.py`

**Changes:**

1. **Transport state files**: Written by `_write_transport_state()`, cleaned up in the `finally` block and by `ai ps cron`.

2. **Enhance `ai ps`** (if it exists as a function, or the process hygiene cron): Add cleanup of stale `transport-*.json` files where `parent_pid` is dead.

3. **Enhance `ai reconnect`**: Read transport state files to annotate output with current transport type for connected sessions.

### Phase 3: Robustness

**Files:** `src/ai_cli/main.py`

**Changes:**

1. **Retry with backoff**: When SSH fails during VPN-active state, retry up to 3 times with 1s/2s/4s delays before giving up.

2. **Debounce (optional)**: If VPN flapping proves annoying, add a 2-second debounce to the watcher thread.

3. **iTerm2 re-emit**: Call `_emit_iterm2_profile_setup()` before each transport launch in the loop (not just at initial launch). This ensures the tab title/color are correct after a mosh->SSH switch and back.

### Test Plan

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_transport_loop_starts_mosh_when_no_vpn` | Unit | Loop uses mosh_args when `_is_vpn_active()` returns False |
| `test_transport_loop_starts_ssh_when_vpn_active` | Unit | Loop uses ssh_args when `_is_vpn_active()` returns True |
| `test_transport_loop_switches_on_vpn_change` | Unit | Watcher thread terminates child, loop reconnects with new transport |
| `test_transport_loop_clean_exit` | Unit | Normal exit (elapsed > 3s) breaks the loop |
| `test_transport_loop_fast_fail_retry` | Unit | Mosh failing <10s with VPN active triggers SSH |
| `test_transport_state_file_written` | Unit | State file created on transport start |
| `test_transport_state_file_cleaned` | Unit | State file deleted on clean exit |
| `test_vpn_watcher_thread_polls` | Unit | Thread calls `_is_vpn_active()` at the configured interval |
| `test_vpn_watcher_thread_terminates_proc` | Unit | Thread calls `proc.terminate()` on VPN state change |
| `test_vpn_flap_uses_latest_state` | Unit | Rapid VPN changes result in correct transport at reconnect |

All tests mock `_is_vpn_active()`, `subprocess.Popen`, and file I/O. No real network calls.

---

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | Mosh-client ignores SIGTERM | Transport switch hangs | Use SIGTERM, then SIGKILL after 2s timeout. Popen.terminate() sends SIGTERM; add a timed kill path. |
| 2 | VPN detection false positive (utun interface from non-VPN) | Unnecessary switch to SSH | Mullvad CLI check is authoritative when available. utun fallback has been reliable in practice. Monitor and add allowlist if needed. |
| 3 | Thread safety: Popen object accessed from two threads | Race condition / crash | `proc_lock` mutex around all Popen access (terminate, poll, assignment). |
| 4 | Background thread prevents Python exit | Zombie process | Thread is `daemon=True` -- dies with parent. `shutdown` event for clean exit in `finally`. |
| 5 | User intentionally on VPN + SSH and doesn't want switch-back | Unwanted mosh reconnect when VPN drops | Add `[remote] vpn_transport_switching = true` config key (default true). Also: if transport is configured as `ssh` (not `mosh`), skip the entire switching mechanism. |

---

## Open Questions

1. **Should debounce be in v1?** A 2-second debounce in the watcher thread would prevent unnecessary reconnects during VPN flaps. Cost: ~10 lines. Risk of not having it: occasional double-reconnect during flaps.

2. **Should the watcher log state transitions?** Writing VPN state changes to `~/.local/state/ai-cli-utils/vpn-transitions.log` would help debug issues. Adds ~5 lines. Could also publish a NATS event for the telemetry system.

3. **`mosh --predict` flag after switch-back?** When switching from SSH back to mosh, should we add `--predict=always` to get faster perceived responsiveness during the initial prediction-training period? Mosh prediction is based on accumulated keystroke timing.

4. **What about WireGuard/other VPN clients?** The `_is_vpn_active()` fallback (utun/tun interface scan) covers most VPN clients on macOS. Should we test with WireGuard specifically, or is the current detection sufficient?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. Debounce in v1?
> 2. Logging state transitions?
> 3. Mosh predict flag?
> 4. WireGuard testing?
> - <enter feedback here>

---

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
