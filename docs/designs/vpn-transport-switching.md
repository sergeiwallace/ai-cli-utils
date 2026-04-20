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

```text
iTerm2 pane
  +-- ai c N -R  (Python process, PID A)
        +-- mosh-client user@host  (PID B, blocks Python via subprocess.run)
              +-- [mosh protocol over UDP to remote mosh-server]
                    +-- tmux session c-r-prefix-N on Hetzner
                          +-- bash -> claude
```text

When mosh dies, `subprocess.run()` returns to the Python process. This is the key leverage point.

---

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Where to put the VPN watcher | (a) Circus watcher, (b) Inline in transport loop, (c) Standalone daemon | **(a) Circus watcher** | Circus is a core architecture foundation; single watcher serves all sessions; supervised | **Approved** |
| 2 | How to kill the active transport | (a) SIGTERM to child via NATS signal, (b) Transport loop self-manages | **(a) SIGTERM to child** | Transport loop subscribes to NATS `vpn.state.changed`, self-terminates child | **Approved** |
| 3 | How to track transport state | (a) PID files in XDG state dir, (b) In-memory only | **(a) State JSON files** | External visibility for `ai ps`, `ai reconnect`, and Circus watcher | **Approved** |
| 4 | VPN watcher lifecycle | (a) One global Circus watcher, (b) Per-session watcher | **(a) One global watcher** | Lazy start on first remote session; shared by all; torn down when last session exits | **Approved** |

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

**✅ Approved: (a) Circus watcher.** Circus is a core architecture foundation (alongside NATS and ntfy), not an incidental dependency. A single global `vpn-watch` Circus watcher polls `_is_vpn_active()` every 3 seconds and publishes `vpn.state.changed` to NATS on transition. Each transport loop subscribes to that NATS subject and self-terminates its child transport process in response. The watcher is started lazily when the first `ai c -R` session launches and torn down when the last session exits (via reference counting in the state files). Circus supervision ensures the watcher restarts on crash.

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
```text

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

**✅ Approved: (a) One global Circus watcher.** Started lazily when first `ai c -R` session launches. Each session registers itself in a state file; the watcher reads these to know which sessions are active. When the last session exits it de-registers, and the watcher shuts itself down (or a Circus `stop` call removes it). VPN state is machine-wide — one poll loop is correct and efficient regardless of session count (5-10+ sessions common).

---

> **Feedback Round 1 — 2026-04-06:**
> D-1: Circus watcher approved. Circus is a core foundation component.
> D-2: SIGTERM to child via NATS approved.
> D-3: State JSON files approved.
> D-4: Single global Circus watcher, lazy start, approved.

---

## Switching Mechanism

### Components

**`vpn-watch` Circus watcher** (new file: `src/ai_cli/vpn_watch.py`):
- Polls `_is_vpn_active()` every 3 seconds
- On state change: waits 2 seconds (debounce), re-checks, then publishes `vpn.state.changed` to NATS with payload `{"vpn": true/false, "ts": "..."}` if state is confirmed
- Logs each transition to `~/.local/state/ai-cli-utils/vpn-transitions.log` (JSONL)
- Started lazily by first `ai c -R` session via `CircusClient.add_watcher()`; stopped when last session de-registers

**Transport loop** (replaces lines 2982-3010 in `main.py`):
- Subscribes to `vpn.state.changed` on NATS at startup
- Runs `subprocess.Popen` for the current transport, waits on it
- NATS message received → call `proc.terminate()` → loop back, pick new transport based on current VPN state
- Writes/deletes transport state JSON file around each `Popen` lifecycle

### Transport Loop (Replaces Current Linear Logic)

The current code at lines 2982-3010 is a linear if/else. The new design replaces this with a **transport loop** driven by NATS:

```python
async def _run_transport_loop(
    ssh_args: list[str],
    mosh_args: list[str],
    cleanup_cmd: list[str],
    session_name: str,
    config: dict,
) -> None:
    state_dir = get_xdg_state_home()
    transport_file = state_dir / f"transport-{session_name}.json"

    nc = await nats.connect(config["messaging"]["nats_servers"])
    vpn_changed = asyncio.Event()

    async def _on_vpn_change(msg):
        vpn_changed.set()

    await nc.subscribe("vpn.state.changed", cb=_on_vpn_change)

    try:
        while True:
            vpn_active = _is_vpn_active()
            vpn_changed.clear()

            args = ssh_args if vpn_active else mosh_args
            transport_type = "ssh" if vpn_active else "mosh"
            print(f"{'VPN active' if vpn_active else 'No VPN'} -- "
                  f"connecting via {transport_type}...", file=sys.stderr)

            proc = subprocess.Popen(args)
            _write_transport_state(transport_file, session_name,
                                   os.getpid(), proc.pid, transport_type)

            start_time = time.monotonic()
            # Wait for process exit OR vpn_changed signal
            while proc.poll() is None:
                if vpn_changed.is_set():
                    proc.terminate()
                    break
                await asyncio.sleep(0.5)
            proc.wait()
            elapsed = time.monotonic() - start_time

            if vpn_changed.is_set():
                print("\nVPN state changed -- switching transport...", file=sys.stderr)
                continue

            if transport_type == "mosh" and elapsed < 10 and _is_vpn_active():
                print(f"\nmosh failed ({elapsed:.1f}s), VPN detected -- "
                      f"switching to SSH...", file=sys.stderr)
                continue

            if elapsed < 3:
                print(f"\nTransport exited too quickly ({elapsed:.1f}s) -- "
                      f"giving up.", file=sys.stderr)
                break

            break  # Normal exit
    finally:
        transport_file.unlink(missing_ok=True)
        await nc.drain()
        subprocess.run(cleanup_cmd, capture_output=True)
```text

### Circus Watcher Lifecycle

```bash
ai c N -R launched
  → _ensure_vpn_watcher(config)
      → reads transport state files to count active sessions
      → if count == 0: CircusClient.add_watcher("vpn-watch", cmd="ai vpn-watch")
  → registers own transport-{session}.json
  → runs transport loop (subscribes to vpn.state.changed)

ai c N -R exits
  → transport-{session}.json deleted (finally block)
  → _maybe_stop_vpn_watcher(config)
      → if no transport-*.json remain: CircusClient.stop("vpn-watch")
```text

`ai vpn-watch` is a new subcommand (entry point for the Circus watcher process).

### State Transitions

```text
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
```text

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
```text

Written when a transport process starts, deleted on clean exit (in `finally` block). Stale files (where `parent_pid` is dead) are cleaned up by `ai ps cron`.

### Integration with `ai ps`

The `ai ps` command can read transport state files to show:
```text
c-r-sw-1  mosh  178.104.70.139  pid=12346  2h uptime
c-r-sw-2  ssh   178.104.70.139  pid=12350  5m uptime (VPN active)
```text

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

### Phase 1: Circus Watcher + NATS Publisher

**New file:** `src/ai_cli/vpn_watch.py`

1. `run_vpn_watch(config)` — entry point for `ai vpn-watch` subcommand
2. Polls `_is_vpn_active()` every `[remote] vpn_poll_interval` seconds (default 3)
3. On state change: 2s debounce, re-check, publish `vpn.state.changed` to NATS
4. Logs each confirmed transition to `~/.local/state/ai-cli-utils/vpn-transitions.log` (JSONL: `{"ts": "...", "vpn": true/false}`)

**Changes to `src/ai_cli/main.py`:**

1. Add `ai vpn-watch` dispatch (~5 lines)
2. Add `_ensure_vpn_watcher(config)` — starts watcher via `CircusClient.add_watcher()` if no transport state files exist yet
3. Add `_maybe_stop_vpn_watcher(config)` — stops watcher via `CircusClient.stop()` if no transport state files remain
4. Add `[remote] vpn_poll_interval = 3` to `DEFAULT_CONFIG`

**Estimated scope:** ~80 lines new, across 2 files.

### Phase 2: Transport Loop Refactor (Core)

**Changes to `src/ai_cli/main.py`:**

1. **`_run_transport_loop()`** (~90 lines): Replaces lines 2982-3010. Subscribes to `vpn.state.changed` on NATS. Runs `Popen` + poll loop, terminates child on NATS message, reconnects with correct transport.
2. **`_write_transport_state()`** (~15 lines): Writes `transport-{session}.json`.
3. **`_emit_iterm2_profile_setup()` call** at top of each loop iteration — ensures tab title/color correct after mosh↔SSH switch.
4. Call `_ensure_vpn_watcher()` at session start, `_maybe_stop_vpn_watcher()` in `finally`.

**Estimated scope:** ~120 new lines, ~30 removed.

### Phase 3: State File Integration + Observability

1. **`ai ps` cleanup**: Add stale `transport-*.json` cleanup (parent PID dead)
2. **`ai reconnect` annotation**: Show current transport type per session from state files
3. **SSH retry with backoff**: On SSH failure during VPN-active, retry 3× (1s/2s/4s) before giving up

### Test Plan

| Test | File | What it verifies |
|------|------|-----------------|
| `test_vpn_watch_publishes_on_state_change` | `test_vpn_watch.py` | NATS publish fired when `_is_vpn_active()` changes |
| `test_vpn_watch_debounce_suppresses_flap` | `test_vpn_watch.py` | No publish if state reverts within 2s |
| `test_vpn_watch_logs_transition` | `test_vpn_watch.py` | JSONL log entry written on confirmed transition |
| `test_transport_loop_starts_mosh_when_no_vpn` | `test_transport.py` | Loop uses mosh_args when VPN inactive |
| `test_transport_loop_starts_ssh_when_vpn_active` | `test_transport.py` | Loop uses ssh_args when VPN active |
| `test_transport_loop_switches_on_nats_message` | `test_transport.py` | NATS message triggers proc.terminate(), loop reconnects |
| `test_transport_loop_clean_exit` | `test_transport.py` | Normal exit (elapsed > 3s) breaks the loop |
| `test_transport_loop_fast_fail_with_vpn` | `test_transport.py` | Mosh <10s + VPN active → SSH |
| `test_transport_state_file_written_and_cleaned` | `test_transport.py` | State file lifecycle |
| `test_ensure_vpn_watcher_starts_circus` | `test_transport.py` | CircusClient.add_watcher called on first session |
| `test_maybe_stop_vpn_watcher_stops_circus` | `test_transport.py` | CircusClient.stop called when last session exits |

All tests mock `_is_vpn_active()`, `subprocess.Popen`, NATS client, and Circus client. No real network calls.

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

1. ~~**Should debounce be in v1?**~~ **✅ Yes — include debounce in v1.** 2-second debounce in the watcher before signaling prevents churn during VPN flaps.

2. ~~**Should the watcher log state transitions?**~~ **✅ Yes — logging and observability are mandatory.** Write VPN state transitions to `~/.local/state/ai-cli-utils/vpn-transitions.log` (JSONL). Publish `vpn.state.changed` to NATS for telemetry. This is a platform-wide design mandate: any sufficiently complex sub-system must include structured logging and telemetry hooks from the start.

3. ~~**`mosh --predict` flag after switch-back?**~~ **Skipped.** Difference is imperceptible in practice (a few seconds of reduced prediction). Not worth the complexity.

4. ~~**WireGuard/other VPN clients?**~~ **Resolved.** Current `_is_vpn_active()` covers Mullvad (CLI check) and Cloudflare WARP + others via `utun`/`tun` interface scan. Sufficient for all currently used VPNs. Client-specific implementations added as needed.

> **Feedback Round 1 — 2026-04-06:**
> OQ-1: Debounce in v1 — approved.
> OQ-2: Logging/observability — approved. Platform-wide mandate added to projects-wide CLAUDE.md.
> OQ-3: Skipped — not worth the complexity.
> OQ-4: Current detection sufficient; handle specifics as needed.

---

## Approval Log

| Date | Round | Decision | Notes |
|------|-------|----------|-------|
| 2026-04-06 | 1 | All 4 decisions approved | Circus watcher + NATS pub/sub + state JSON files + single global watcher with lazy start |
| 2026-04-06 | 1 | Open questions resolved | Debounce in v1; logging/observability mandatory; mosh predict skipped; current VPN detection sufficient |
