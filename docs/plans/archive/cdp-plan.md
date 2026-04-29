---
title: "CDP Browser Debug Server — Implementation Plan"
category: plan
tags: [cdp, chrome, devtools, browser, debugging]
status: archived
---

# CDP Browser Debug Server — Implementation Plan

**Status:** DRAFT

**Created:** 2026-04-05

**Task:** `[AI-CLI-34]`

## Table of Contents

- [Overview](#overview)
- [Options](#options)
- [Technical Design](#technical-design)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Add `ai cdp start` / `ai cdp stop` subcommands to launch and kill a Chrome/Chromium instance
with the Chrome DevTools Protocol (CDP) remote debugging endpoint exposed. This lets
agent-browser, Playwright, and other CDP-speaking tools attach to a browser session without
manually managing Chrome's flags or port.

**Scope:** start, stop, and status lifecycle only. No interaction with the CDP API itself.
Follows the same PID-file + XDG state dir pattern used by `ai tunnel start/stop`.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Options

### Option A: Subprocess.Popen (background) with PID file

Launch Chrome via `subprocess.Popen(..., start_new_session=True)` so it detaches and runs
in the background. Write PID to `~/.local/state/ai-cli-utils/cdp-<port>.pid`. Readiness
check polls `localhost:<port>/json/version` up to 5 s.

**Pros:**
- Identical lifecycle pattern to `ai tunnel start/stop` — minimal new code surface
- Chrome log output is suppressed (goes to DEVNULL) — clean terminal
- PID file enables clean stop and idempotent start checks
- Cross-platform: works on macOS and Linux

**Cons:**
- Log output not visible (user can't see Chrome stderr)
- Background process may leave orphans if not stopped cleanly

### Option B: Foreground subprocess (blocking)

Run Chrome in the foreground so log output is visible. Process stays alive until Ctrl-C.
No PID file needed — stop = Ctrl-C.

**Pros:**
- Log output is visible in terminal
- No orphan risk — terminal session cleanup handles it

**Cons:**
- Ties up the terminal pane; `ai cdp stop` has nothing to stop from another pane
- Can't use idempotent start check (no PID file)
- Mismatches the spec: spec says "runs in the foreground so log output is visible" —
  but also says `ai cdp stop [--port 9222] — kills the Chrome process on that port`,
  implying the process can be killed from elsewhere → PID file is still needed

### Option C: Option A + optional foreground flag

Default: background (Option A) + PID file. Add `--foreground` / `-f` flag to run in the
foreground for interactive debugging. When `--foreground` is used, skip PID file write;
`ai cdp stop` falls back to pkill-by-port.

**Pros:**
- Covers both the "inspect CDP logs" and "run and forget" use cases
- `stop` command works in both modes

**Cons:**
- Extra flag, extra code path, more to test

### Recommendation

**Option A** — background with PID file. The spec says "runs in the foreground so log output
is visible" but simultaneously requires `ai cdp stop`, which is only meaningful for a
background process. Interpreting the intent as "Chrome output should be capturable if needed"
rather than "must block the terminal." If the user clarifies they want foreground blocking,
promote to Option C.

## Technical Design

### Command signatures

```bash
ai cdp start [--port 9222] [--no-incognito]
ai cdp stop  [--port 9222]
ai cdp status
```text

### Chrome binary detection (cross-platform)

```python
def _find_chrome_binary(config: dict) -> str | None:
    # 1. Explicit config override
    configured = config.get("cdp", {}).get("binary_path", "")
    if configured:
        return configured if Path(configured).exists() else None

    # 2. Well-known paths by platform
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform.startswith("linux"):
        candidates = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    elif sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]

    for c in candidates:
        found = shutil.which(c) or (Path(c).exists() and c)
        if found:
            return str(found)
    return None
```text

### Start function

```python
def _cmd_cdp_start(port: int, incognito: bool, config: dict) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"

    # Idempotent: check if already running
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            print(f"CDP already running on port {port} (PID {pid})")
            return
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    chrome = _find_chrome_binary(config)
    if not chrome:
        print(
            "Chrome/Chromium not found. Install it or set [cdp] binary_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={tempfile.gettempdir()}/chrome-debug-{port}",
    ]
    if incognito:
        cmd.append("--incognito")

    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid))

    # Readiness poll: up to 5 s
    url = f"http://localhost:{port}/json/version"
    deadline = time.monotonic() + 5.0
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)  # noqa: S310
            ready = True
            break
        except Exception:
            time.sleep(0.25)

    if ready:
        print(f"CDP ready at localhost:{port}")
    else:
        print(f"CDP started (PID {proc.pid}) — endpoint not yet responding on port {port}")
```text

### Stop function

```python
def _cmd_cdp_stop(port: int) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"
    if not pid_file.exists():
        print(f"No CDP process registered on port {port}.")
        return
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
    pid_file.unlink(missing_ok=True)
    print(f"CDP stopped: port {port}")
```text

### Status function

```python
def _cmd_cdp_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("cdp-*.pid"))
    if not pid_files:
        print("No CDP processes registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("cdp-"):]
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            status = "alive"
        except ProcessLookupError:
            status = "dead"
            pid_file.unlink(missing_ok=True)
        print(f"port {port}: PID {pid} ({status})")
```text

### Dispatch block (in `cli()`)

```python
if len(sys.argv) > 1 and sys.argv[1] == "cdp":
    if len(sys.argv) < 3:
        print(
            "Usage: ai cdp [start [--port N] [--no-incognito] | stop [--port N] | status]",
            file=sys.stderr,
        )
        sys.exit(1)
    cdp_action = sys.argv[2]
    if cdp_action == "start":
        _cdp_parser = argparse.ArgumentParser()
        _cdp_parser.add_argument("-p", "--port", type=int, default=config.get("cdp", {}).get("port", 9222))
        _cdp_parser.add_argument("-I", "--no-incognito", action="store_true")
        _cdp_args = _cdp_parser.parse_args(sys.argv[3:])
        _cmd_cdp_start(_cdp_args.port, not _cdp_args.no_incognito, config)
        sys.exit(0)
    elif cdp_action == "stop":
        _cdp_parser = argparse.ArgumentParser()
        _cdp_parser.add_argument("-p", "--port", type=int, default=config.get("cdp", {}).get("port", 9222))
        _cdp_args = _cdp_parser.parse_args(sys.argv[3:])
        _cmd_cdp_stop(_cdp_args.port)
        sys.exit(0)
    elif cdp_action == "status":
        _cmd_cdp_status()
        sys.exit(0)
    else:
        print(f"Unknown cdp action: {cdp_action}. Use start, stop, or status.", file=sys.stderr)
        sys.exit(1)
```text

### Config section

Add to `DEFAULT_CONFIG` comment block:

```toml
[cdp]
## Path to Chrome/Chromium binary (auto-detected if not set)
# binary_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
## Default CDP port
# port = 9222
```text

Config access: `config.get("cdp", {}).get("port", 9222)` / `.get("binary_path", "")`.

### Imports needed

- `urllib.request` — stdlib, readiness poll (avoids `requests` dependency)
- `tempfile` — stdlib, user-data-dir path
- `time` — already imported in `main.py`

### Files changed

| File | Change |
|------|--------|
| `src/ai_cli/main.py` | Add `_find_chrome_binary()`, `_cmd_cdp_start()`, `_cmd_cdp_stop()`, `_cmd_cdp_status()`, dispatch block in `cli()`, DEFAULT_CONFIG comment |
| `tests/test_cdp.py` | New test file — full coverage of all four functions |
| `docs/roadmap/master-roadmap.md` | Add `[AI-CLI-34]` entry |
| `docs/tools/ai-cli-usage.md` | Add `ai cdp` command reference |

### Test strategy

Follow `tests/test_main.py::TestCmdTunnelStart/Stop/Status` patterns exactly:

- **`TestFindChromeBinary`** — configured path exists/missing; platform candidates hit/miss; no binary found
- **`TestCmdCdpStart`** — fresh start writes PID file and prints ready; already-running idempotent; no Chrome found exits 1; readiness poll timeout prints warning; readiness poll succeeds prints ready
- **`TestCmdCdpStop`** — terminates process and removes PID; no PID file is a noop; dead process (ProcessLookupError) still removes PID file
- **`TestCmdCdpStatus`** — no PID files; alive process; dead process (unlinks file); multiple ports
- **`TestCdpDispatch`** — `ai cdp start` / `ai cdp stop` / `ai cdp status` dispatch via `cli()`; `ai cdp <unknown>` exits 1; `ai cdp` with no action exits 1; `--port` flag; `--no-incognito` flag

All subprocess calls mocked via `patch("subprocess.Popen")`. Readiness poll mocked via
`patch("urllib.request.urlopen")`. PID lifecycle tested with `tmp_path` + `monkeypatch`.

### Codebase patterns confirmed

| Pattern | Location |
|---------|----------|
| XDG state dir | `get_xdg_state_home()` — line 29 |
| PID file write/read | `_cmd_tunnel_start()` — line 1618–1620 |
| `os.kill(pid, 0)` existence check | `_cmd_tunnel_start()` — line 1580 |
| SIGTERM stop | `_cmd_tunnel_stop()` — line 1658 |
| Glob-based status | `_cmd_tunnel_status()` — line 1667 |
| Dispatch block | `sys.argv[1] == "tunnel"` — line 2334 |
| Config access | `.get("section", {}).get("key", default)` throughout |

## Task Breakdown

### T-01: Core implementation

**Size:** M
**Batch:** 1

Add `_find_chrome_binary()`, `_cmd_cdp_start()`, `_cmd_cdp_stop()`, `_cmd_cdp_status()`
to `main.py` plus the dispatch block in `cli()` and the `[cdp]` section in DEFAULT_CONFIG.

**Deliverables:**

- `src/ai_cli/main.py` — four new functions + dispatch + config comment

**Acceptance criteria:**

- [ ] `ai cdp start` launches Chrome, writes PID, prints "CDP ready at localhost:<port>"
- [ ] `ai cdp start` when already running prints "CDP already running" and exits 0
- [ ] `ai cdp start` with no Chrome binary prints error and exits 1
- [ ] `ai cdp stop` sends SIGTERM and removes PID file
- [ ] `ai cdp stop` when not running prints message and exits 0
- [ ] `ai cdp status` reports alive/dead per PID file
- [ ] `--port` / `-p` and `--no-incognito` / `-I` flags work on start; `--port` / `-p` on stop
- [ ] `[cdp] binary_path` config key overrides auto-detection
- [ ] `[cdp] port` config key sets default port

**Dependencies:** None

### T-02: Tests

**Size:** M
**Batch:** 1 (parallel with T-01)

Full test coverage for all four functions and dispatch.

**Deliverables:**

- `tests/test_cdp.py` — new file, ~120–150 lines

**Acceptance criteria:**

- [ ] All functions covered to 100%
- [ ] All test names follow `test_{given}_{when}_{then}` pattern
- [ ] No real subprocess or network calls (all mocked)
- [ ] `ruff check` + `ruff format --check` pass
- [ ] `pytest` passes locally

**Dependencies:** T-01

### T-03: Docs update

**Size:** S
**Batch:** 2

Update usage reference and roadmap.

**Deliverables:**

- `docs/tools/ai-cli-usage.md` — `ai cdp` section
- `docs/roadmap/master-roadmap.md` — AI-CLI-34 marked in progress → done

**Acceptance criteria:**

- [ ] `ai cdp` documented with all flags, config keys, and examples
- [ ] Roadmap entry complete

**Dependencies:** T-01, T-02

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Implementation + tests | Tests pass, ruff clean |
| 2 | T-03 | Docs | Human UAT approval |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope, option selection, flag names |
| UAT | After Batch 2 | Manual smoke-test: `ai cdp start`, verify Chrome opens, `ai cdp stop` |

## Open Questions

1. **Foreground vs background**: spec says "runs in the foreground so log output is visible"
   but also requires `ai cdp stop`. Recommendation: background + PID file (Option A).
   Confirm or should foreground (`--foreground`) be added as Option C?

2. **Windows support**: `--remote-debugging-port` works on Chrome for Windows, but
   `SIGTERM` (`os.kill(pid, 15)`) is not available on Windows — need `os.kill(pid, 9)` or
   `subprocess.Popen.terminate()`. Should Windows stop be included now or deferred to AI-CLI-29?

3. **`--user-data-dir` cleanup**: `start` creates `/tmp/chrome-debug-<port>/`. `stop` does
   not clean it up (Chrome may still be writing). Add cleanup to `stop`, or leave it as
   ephemeral debris?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- foreground vs background -->
> 2. <!-- windows stop signal -->
> 3. <!-- user-data-dir cleanup on stop -->
> - <enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
