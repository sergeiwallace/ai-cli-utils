---
title: "Windows Out-of-Box Support — Implementation Plan"
category: plan
tags: [windows, portability, cross-platform, AI-CLI-29]
status: draft
---

# Windows Out-of-Box Support — Implementation Plan

**Status:** DRAFT — awaiting user review before implementation

**Created:** 2026-04-24

**Task:** `[AI-CLI-29]` `[P0]`

## Table of Contents

- [Overview](#overview)
- [Scope](#scope)
- [Options](#options)
- [Technical Design](#technical-design)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

The `ai-cli-utils` package currently fails to import on Windows due to a top-level `import fcntl`
in `iterm2.py`. Beyond import failures, POSIX path assumptions (`~/.config`, `~/.local/state`)
and shell-specific subprocess calls mean the core commands are non-functional even if the import
were fixed.

Goal: `pip install ai-cli-utils` on Windows + `ai` should work for the portable subset of
features (quota display, Gemini CLI, sync, notifications) without manual path configuration.
macOS-only features (iTerm2 integration, tmux session management, mosh tunneling) are
explicitly out of scope for Windows — they should degrade gracefully rather than crash.

> **Feedback:** Is the "portable subset" framing right? Any Windows-specific features that
> should be added (e.g. Windows Terminal integration, PowerShell support)?
> - <enter feedback here>

## Scope

### In scope

| Area | What changes |
|------|-------------|
| XDG path abstraction | `config.py` — use `%APPDATA%`/`%LOCALAPPDATA%` on Windows |
| `fcntl` import guard | `iterm2.py` — conditional import; lock file uses `msvcrt` or `portalocker` |
| `/tmp` hardcodes | `notifications.py` — replace `/tmp/...` with `tempfile.gettempdir()` |
| `/dev/null` hardcodes | `gemini.py`, `research.py` — replace with `os.devnull` |
| Process existence checks | `tunnel.py`, `sync.py` — replace `os.kill(pid, 0)` with `psutil` |
| OS notifications | `notifications.py` — add `win32` branch (PowerShell toast) |
| CI matrix | `.github/workflows/ci.yml` — add `windows-latest` runner |
| iTerm2 guards | `iterm2.py` — wrap all functions with `sys.platform == "darwin"` guard at call sites |

### Out of scope

- tmux session management (`ai c`, `ai g`) — requires tmux, not available on Windows
- mosh / SSH transport — POSIX-specific; document as macOS/Linux only
- Bash session script (`session_script.py`) — bash-dependent; document as unsupported
- signal-watch NATS daemon — POSIX signal infrastructure; document as unsupported
- Full parity on all features — goal is graceful degradation, not a full Windows port

> **Feedback:** Agree with the in/out-of-scope line? Anything that should move between lists?
> - <enter feedback here>

## Options

### Option A: Minimal portability fixes only (no new abstraction layer)

Fix the blocking issues file-by-file with targeted conditionals (`if sys.platform == "win32"`).
No shared abstraction — each module handles its own platform branching.

**Pros:**
- Smallest changeset — minimal risk of regressions
- No new public API surface to maintain
- Fastest to ship

**Cons:**
- Platform branches scattered across 8+ files — hard to audit
- Duplicated platform-detection logic
- CI failures on Windows will be harder to attribute

### Option B: Path abstraction layer in `config.py` + targeted fixes elsewhere

Centralize OS-aware path resolution in `config.py` (already owns XDG functions). All other
modules call `config.py` helpers rather than hard-coding paths. Non-path issues (fcntl,
signals, /dev/null) fixed in-place with platform conditionals.

**Pros:**
- Platform logic for paths is in one place — easier to test and audit
- `config.py` already owns this responsibility; no new module needed
- Other fixes remain small and self-contained

**Cons:**
- Slightly larger change to `config.py`
- Tests for path resolution need to cover both platforms

### Option C: Full `portalocker` + `psutil` dependency introduction

Add `portalocker` (cross-platform file locking) and `psutil` (cross-platform process checks)
as hard dependencies. Use them everywhere instead of `fcntl` / `os.kill`.

**Pros:**
- Cleanest cross-platform solution — no manual branching for locking or signals
- `psutil` and `portalocker` are well-maintained and widely used

**Cons:**
- Adds 2 new hard dependencies (heavier install)
- `psutil` installs native extensions — may complicate lightweight installs
- Overkill for a package where Windows support is currently secondary

### Recommendation

**Option B** — path abstraction in `config.py` + targeted in-place fixes for the remaining
issues. This gives a clean, auditable path layer without adding hard dependencies. The `fcntl`
guard and `/dev/null` / `/tmp` replacements are small enough to fix in-place. If the project
later needs `psutil` for other reasons, the `os.kill(pid, 0)` calls can migrate then.

> **Feedback:** Option B recommended. Agree? Any preference for Option C (portalocker/psutil)?
> - <enter feedback here>

## Technical Design

### 1. XDG Path Abstraction (`config.py`)

Replace the current XDG-only helpers with platform-aware versions:

```python
def get_xdg_config_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(base) / "ai-cli-utils"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ai-cli-utils"

def get_xdg_state_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "ai-cli-utils"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "ai-cli-utils"
```

All modules already call these helpers — no cascading changes needed.

### 2. `fcntl` Guard (`iterm2.py`)

`fcntl` is used only for the color-lease lock file. On Windows, iTerm2 integration is a no-op
(`_is_iterm2()` returns `False`), so the lock is never reached. Guard at import time:

```python
try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False
```

Wrap the `flock` calls: `if _HAS_FCNTL: _fcntl.flock(...)`. On Windows these are no-ops —
the iTerm2 guard above ensures this code path is never reached on Windows in production.

### 3. Hardcoded `/tmp` and `/dev/null`

| File | Current | Fix |
|------|---------|-----|
| `notifications.py:248` | `f"/tmp/ai-batch-{session_id}.lock"` | `Path(tempfile.gettempdir()) / f"ai-batch-{session_id}.lock"` |
| `gemini.py:1016` | `"/dev/null"` string comparison | `os.devnull` |
| `research.py:546` | `"/dev/null"` string comparison | `os.devnull` |

### 4. Process Existence Checks (`tunnel.py`, `sync.py`)

Replace `os.kill(pid, 0)` (POSIX-only) with a portable alternative that avoids the `psutil`
dependency:

```python
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        # Windows: os.kill raises OSError for non-existent PIDs
        return False
```

`os.kill(pid, 0)` actually works on Windows in Python 3.8+ for existence checks — it raises
`OSError` for missing PIDs rather than `ProcessLookupError`. A unified helper covers both.

### 5. OS Notifications (`notifications.py`)

Add a `win32` branch in `_send_os_notification()`:

```python
elif sys.platform == "win32":
    subprocess.run([
        "powershell", "-Command",
        f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; ..."
    ], capture_output=True)
```

Alternative: use `plyer` (optional extra `ai-cli-utils[notify-win]`) to avoid PowerShell complexity.

### 6. iTerm2 Guards

`_is_iterm2()` already returns `False` on non-macOS. The AppleScript call in
`_set_iterm2_name_applescript` already has `if sys.platform != "darwin": return`. The only
remaining gap is the top-level `import fcntl` — covered in item 2 above.

### 7. CI Matrix (`.github/workflows/ci.yml`)

```yaml
strategy:
  matrix:
    python-version: ["3.11", "3.12", "3.13"]
    os: [ubuntu-latest, windows-latest, macos-latest]
```

iTerm2 and tmux tests already skip when the relevant env vars are absent — they will skip
cleanly on Windows CI. Tests that use `fcntl` or POSIX signals directly will need `@pytest.mark.skipif(sys.platform == "win32", ...)` guards.

## Task Breakdown

| # | Task | Files | Notes |
|---|------|-------|-------|
| T-01 | Platform-aware path helpers in `config.py` | `config.py`, `tests/test_config.py` | Foundation — do first |
| T-02 | Guard `fcntl` import in `iterm2.py` | `iterm2.py`, `tests/test_iterm2.py` | Unblocks Windows import |
| T-03 | Replace `/tmp` and `/dev/null` hardcodes | `notifications.py`, `gemini.py`, `research.py` | Small, low-risk |
| T-04 | Portable `_pid_alive()` helper | `tunnel.py`, `sync.py` | Shared helper in `config.py` or new `process.py` |
| T-05 | Windows OS notification branch | `notifications.py` | Low priority; can degrade gracefully |
| T-06 | CI matrix expansion to `windows-latest` | `.github/workflows/ci.yml` | Add after T-01–T-04 pass locally |
| T-07 | Tests: platform-specific skipif guards | `tests/` | Co-ship with each fix |
| T-08 | Docs: document Windows-unsupported features | `README.md`, `docs/tools/ai-cli-usage.md` | Same commit as T-06 |

## Batch Plan

**Batch 1 (foundation):** T-01, T-02, T-03 — the three changes that unblock `import ai_cli` on Windows.

**Batch 2 (hardening):** T-04, T-05 — process checks and notifications.

**Batch 3 (CI + docs):** T-06, T-07, T-08 — wire Windows into CI and document scope.

## Human Gates

| Gate | Before | Action |
|------|--------|--------|
| **Plan review** | Before any code | User approves this doc |
| **Batch 1 UAT** | After Batch 1 ships | Verify `pip install` + `ai quota status` works on Windows |
| **CI green** | After Batch 3 | Confirm `windows-latest` runner passes before closing task |

## Open Questions

1. **T-05 PowerShell vs plyer** — Is a `[notify-win]` optional extra acceptable, or should Windows toast notifications be dependency-free (PowerShell script)?
2. **Test coverage on Windows** — Some tests use `fcntl` directly (not through the module being tested). Should these be marked `skipif win32` or refactored?
3. **`ai c` / `ai g` on Windows** — Should the session-launch commands print a clear "not supported on Windows" error rather than crashing on the bash template? Recommendation: yes — add a platform guard at the top of `_do_session_launch`.
4. **Version bump** — This is a significant portability fix. Minor bump (`0.6.0`) or patch (`0.5.x`)?

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
| — | — | — |
