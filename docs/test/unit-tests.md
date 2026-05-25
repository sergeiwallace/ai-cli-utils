# Unit Tests

> Track all unit tests. Each entry maps to an acceptance criterion.

## Table of Contents

- [Template](#template)
- [Intentionally Uncovered Lines](#intentionally-uncovered-lines)

## Template

| Field | Value |
|-------|-------|
| **Test name** | `test_...` |
| **File** | `path/to/test.rs` |
| **Acceptance criterion** | GIVEN ... WHEN ... THEN ... |
| **Status** | Passing / Failing / Not written |

---

## Intentionally Uncovered Lines

These lines are not covered by unit tests and are intentionally left without `# pragma: no cover`.
Each has an inline `# Not covered:` comment at the relevant site. They fall into two categories:
live-infrastructure boundaries (NATS server required) and live-network-state boundaries (VPN + mosh).

**Policy:** do not add `# pragma: no cover` without explicit human approval. If coverage tooling
flags these, the inline comments document the reason. The code path itself is correct and tested
at the integration level when the full stack is running.

Last verified against: 2026-04-06 (after VPN transport switching feature). Line numbers shift with edits — use the
code snippets below to relocate each site after future changes.

---

### Group A — `src/ai_cli/main.py`: async NATS closures (≈30 lines)

**Why uncovered:** These closures are defined inside `cli()`, capture outer-scope session
variables, and are only invoked when a live NATS JetStream message arrives. Unit tests mock
`NATSClient` at the transport layer; the callbacks themselves never execute in the test harness.
Extracting them to module-level (Option B in the pre-release plan) would allow injection, but
adds scope beyond the current release. A NATS container in CI (Option C) would cover Group A
but not B or C.

#### `_on_handoff` closure — `ai internal signal-watch` path

```python
# Not covered: entire _on_handoff closure is only invoked when a live NATS
# JetStream message arrives. Requires a real NATS server + network delivery.
async def _on_handoff(data):
    ...
    if not local_file.exists():
        pending_dir.mkdir(parents=True, exist_ok=True)
        try:
            local_file.write_text(content)
        except OSError:          # ← not covered
            pass
```text

Locate: search for `async def _on_handoff` inside the `action == "signal-watch"` block.

#### Startup scan exception handlers — `ai internal signal-watch` path

```python
for f in sorted(pending_dir.glob("*.md")):
    try:
        fid = int(f.name.split("-")[0])
    except ValueError:           # ← not covered
        continue
    try:
        raw = f.read_text()
        ...
    except OSError:              # ← not covered
        scan_title, scan_priority, body, scan_for_machine = f.stem, "", "", ""
```text

Locate: inside the startup-scan `for f in sorted(pending_dir.glob(...))` loop,
in the `action == "signal-watch"` block.

#### `subscribe_durable` exception — `ai internal signal-watch` path

```python
try:
    asyncio.run(sw_client.subscribe_durable(..., _on_handoff))
except Exception:
    # Not covered: subscribe_durable blocks indefinitely on success; exception
    # path requires a live NATS server to fail mid-subscription.
    pass
```text

#### `_write_pending_if_claimed` closure — `ai internal handoff-drain` path

```python
# Not covered: only reachable via _drain() (live NATS) or local-scan (covered).
# Inner branches require live handoff delivery or specific filesystem failures.
def _write_pending_if_claimed(data):
    ...
    if not for_machine or for_machine != os.environ.get("AI_HOST", ""):
        return False             # ← not covered via NATS path
    if hd_handoff_dir is None or not handoff_id:
        return False             # ← not covered via NATS path
    if content and filename:
        ...
        try:
            local_file.write_text(content)
        except OSError:
            return False         # ← not covered
    claimed = _claim_handoff_for_signal(...)
    if claimed is None:
        return False             # ← not covered via NATS path
```text

#### Local-scan filesystem error — `ai internal handoff-drain` path

```python
try:
    fid = int(best.name.split("-")[0])
    raw = best.read_text()
    ...
    _write_pending_if_claimed(...)
except Exception:
    # Not covered: requires filesystem error reading a pending handoff file
    # that exists and was just discovered by glob.
    pass
```text

#### `_drain()` closure — `ai internal handoff-drain` path

```python
# Not covered: async closure requiring live NATS JetStream server.
async def _drain():
    ...
    if not hd_client.js:         # ← not covered
        return
    ...
    try:
        msgs = await sub.fetch(1, timeout=2)
        for msg in msgs:
            try:
                data = json.loads(msg.data.decode())
            except Exception:    # ← not covered
                data = {}
            await msg.ack()
            if _write_pending_if_claimed(data):  # ← not covered (True branch)
                return
    except Exception:            # ← not covered (inner fetch loop)
        break
    except Exception as e:       # ← not covered (subscribe failure)
        _log_handoff_event(...)
```text

#### `asyncio.run(_drain())` exception

```python
try:
    asyncio.run(_drain())
except Exception as e:
    # Not covered: requires asyncio.run() itself to raise — broken event loop
    # or NATS server in a specific failure state.
    _log_handoff_event("handoff.drain.nats_run_failed", ...)
```text

---

### Group B — `src/ai_cli/main.py`: transport loop exception handlers and SSH retry edge cases

**Why uncovered:** These paths require specific combinations of live NATS failures, subprocess
timeout expiry, or VPN state changes occurring mid-retry. All require tightly-timed concurrent
events that are impractical to simulate reliably in unit tests.

#### `except Exception: pass` in `_ensure_vpn_watcher` status check

```python
try:
    result = client.send_message("status")
    ...
except Exception:
    # Not covered: requires CircusClient.send_message to raise mid-call.
    pass
```text

#### `except Exception: pass` in `_run_transport_loop` NATS subscribe

```python
try:
    await nc.nc.subscribe("vpn.state.changed", cb=_on_vpn_change)
except Exception:
    # Not covered: requires NATS subscribe to raise after connect succeeds.
    pass
```text

#### `proc.kill()` after `subprocess.TimeoutExpired`

```python
try:
    proc.wait(timeout=2)
except subprocess.TimeoutExpired:
    proc.kill()       # ← not covered
    proc.wait()       # ← not covered
```text

#### SSH retry inner-loop edge cases (VPN changes during retry, SSH succeeds on retry)

```python
for delay in (1, 2, 4):
    ...
    while proc2.poll() is None:
        if vpn_changed.is_set():
            proc2.terminate()   # ← not covered (VPN change during retry)
            proc2.wait()        # ← not covered
            break               # ← not covered
        ...
    if proc2.returncode == 0:
        return                  # ← not covered (SSH retry succeeds)
    if vpn_changed.is_set():
        print(...)              # ← not covered
        break                   # ← not covered
if vpn_changed.is_set():
    continue                    # ← not covered
```text

#### `nc.close()` exception in transport loop finally

```python
try:
    await nc.close()
except Exception:
    # Not covered: requires NATS close to raise after connect succeeds.
    pass
```text

---

### Group D — `src/ai_cli/vpn_watch.py`: exception handlers (3 lines)

**Why uncovered:** These are `except`/`pass` guards around infrastructure boundaries.

```python
try:
    with open(log_file, "a") as f:
        f.write(json.dumps(payload) + "\n")
except OSError:
    pass  # ← not covered: requires filesystem write failure

if nc.nc:
    try:
        await nc.nc.publish(...)
    except Exception:
        pass  # ← not covered: requires NATS publish to raise after subscribe

try:
    await nc.close()
except Exception:
    pass  # ← not covered: requires NATS close to raise after connect
```text

---

### Group C — `src/ai_cli/messaging.py`: NATS error callback (1 line)

**Why uncovered:** `_noop_error_cb` is registered with the NATS client as `error_cb`. It is
invoked by the NATS library internals on connection error — never called from application code.
The body is intentionally empty (`pass`); the function exists solely to suppress default error
logging.

```python
async def _noop_error_cb(e):
    # Not covered: invoked by the NATS client library on connection error —
    # never called from application code directly. Requires a live NATS server.
    pass  # ← not covered
```text

Locate: inside `NATSClient.connect()`, in the retry loop, before the `nats.connect()` call.
