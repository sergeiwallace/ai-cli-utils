---
title: "Windows Out-of-Box Support — Implementation Plan"
category: plan
tags: [windows, portability, cross-platform, AI-CLI-29]
status: complete
---

# Windows Out-of-Box Support — Implementation Plan

**Status:** COMPLETE (2026-04-25)

**Created:** 2026-04-24

**Task:** `[AI-CLI-29]` `[P0]`

## Table of Contents

- [Overview](#overview)
- [Decisions](#decisions)
- [Technical Design](#technical-design)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Approval Log](#approval-log)

## Overview

The `ai-cli-utils` package currently fails to import on Windows due to a top-level `import fcntl`
in `iterm2.py`. Beyond import failures, POSIX path assumptions (`~/.config`, `~/.local/state`)
and shell-specific subprocess calls mean the core commands are non-functional even if the import
were fixed.

Goal: `pip install ai-cli-utils` on Windows + `ai` should work for the full portable feature set
(quota display, Gemini CLI, sync, notifications, `ai c`/`ai g` session management). macOS-only
features (iTerm2 integration) degrade gracefully rather than crash.

**Primary Windows shell target:** Git Bash. PowerShell is a secondary target.

### In scope

| Area | What changes |
|------|-------------|
| XDG path abstraction | `config.py` — use `%APPDATA%`/`%LOCALAPPDATA%` on Windows |
| `fcntl` replacement | `iterm2.py` — replace with `portalocker` |
| `/tmp` hardcodes | `notifications.py` — replace with `tempfile.gettempdir()` |
| `/dev/null` hardcodes | `gemini.py`, `research.py` — replace with `os.devnull` |
| Console encoding | modules that print emoji/Unicode — add `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` guard on Windows |
| Process existence checks | `tunnel.py`, `sync.py` — replace `os.kill(pid, 0)` with `psutil` |
| OS notifications | `notifications.py` — `plyer` optional extra `[notify-win]` |
| `ai c` / `ai g` session management | requires tmux installed via MSYS2; documented |
| CI matrix | `.github/workflows/ci.yml` — add `windows-latest` runner |
| iTerm2 guards | `iterm2.py` — `sys.platform == "darwin"` guard; already mostly present |
| Test cross-platform coverage | no test skipping — platform-specific code mocked |

### Out of scope

- mosh / SSH transport — POSIX-specific; documented as macOS/Linux only
- signal-watch NATS daemon — POSIX signal infrastructure; documented as unsupported on Windows
- Full feature parity — goal is graceful degradation for macOS-only features, not a full port

> **Feedback Round 1:**
> - Scope framing confirmed. `ai c`/`ai g` should work on Windows — moved in scope.
>   User uses Git Bash; tmux is available via MSYS2.
>
> **AI Response Round 1:**
> - `ai c`/`ai g` moved in scope. They invoke `tmux` as a subprocess — works in Git Bash
>   with tmux installed (MSYS2: `pacman -S tmux`). Bash session scripts work in Git Bash.
>   Added T-09 to document the MSYS2 tmux requirement.
> - signal-watch remains out of scope (POSIX signal infrastructure).

## Decisions

### Decision Summary

| # | Decision | Options | Status |
|---|----------|---------|--------|
| D1 | Cross-platform locking and process checks | (a) targeted conditionals, (b) config.py abstraction, (c) portalocker + psutil | `APPROVED: (c)` |
| D2 | Windows OS notifications | (a) PowerShell subprocess, (b) plyer optional extra | `APPROVED: (b)` |
| D3 | Windows shell target | (a) PowerShell primary, (b) Git Bash primary | `APPROVED: (b)` |
| D4 | Version bump | (a) patch 0.5.x, (b) minor 0.6.0 | `APPROVED: (b)` |

---

### D1: Cross-platform locking and process checks — `[APPROVED: (c) portalocker + psutil]`

The package uses `fcntl.flock` for file locking and `os.kill(pid, 0)` for process existence
checks. Both are POSIX-only and fail on Windows.

#### (a) Targeted conditionals

Add `if sys.platform == "win32"` branches in each affected file. No new dependencies.

**Pros:**

- Smallest changeset — zero new dependencies
- Fastest to ship

**Cons:**

- Platform branches scattered across 8+ files — hard to audit
- Duplicated platform-detection logic
- `os.kill` on Windows has subtly different error semantics requiring careful wrapping anyway

#### (b) Path abstraction in `config.py` + in-place fixes elsewhere

Centralize OS-aware path resolution in `config.py`. Fix `fcntl` and `os.kill` in-place with
conditional guards.

**Pros:**

- Path logic in one place
- No new dependencies

**Cons:**

- `fcntl` / `os.kill` still need per-file guards
- Tests still need to branch on platform for locking/process scenarios

#### (c) `portalocker` + `psutil` as hard dependencies

Add `portalocker` (cross-platform file locking) and `psutil` (cross-platform process checks)
as hard dependencies. Replace all `fcntl` and `os.kill` calls with these libraries everywhere.

**Pros:**

- Cleanest solution — no manual branching for locking or process checks
- `psutil` and `portalocker` are well-maintained, widely used, and well-tested on Windows
- Tests run identically on all platforms — no platform guards needed for these code paths

**Cons:**

- Two new hard dependencies (heavier install)
- `psutil` installs a native C extension

#### Recommendation

> **Decision:** `APPROVED — (c) portalocker + psutil`
<!-- decision-record: chosen-option=(c); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

portalocker and psutil are the right tool for this. The native extension in psutil is standard
and present in virtually every Python environment. This avoids scattered conditionals and makes
tests cross-platform by default.

---

### D2: Windows OS notifications — `[APPROVED: (b) plyer optional extra]`

The `notifications.py` module uses OS-native notification APIs. On Windows this requires either
invoking PowerShell or using a cross-platform library.

#### (a) PowerShell subprocess

Invoke PowerShell's `[Windows.UI.Notifications.ToastNotificationManager]` API via subprocess.

**Pros:**

- No additional dependencies
- Works on any Windows 10+ machine

**Cons:**

- Complex PowerShell one-liner that is fragile and hard to test
- Requires PowerShell execution policy to allow scripts
- User prefers Git Bash; PowerShell dependency is undesirable

#### (b) plyer optional extra `[notify-win]`

Use `plyer` (cross-platform notification library) installed as an optional extra.

**Pros:**

- Clean API — one `notification.notify()` call works on Windows, macOS, Linux
- No PowerShell dependency
- Optional — base install stays lean; Windows users who want notifications install `[notify-win]`

**Cons:**

- Adds optional dependency
- `plyer` pulls in some desktop toolkit detection on import

#### Recommendation

> **Decision:** `APPROVED — (b) plyer optional extra [notify-win]`
<!-- decision-record: chosen-option=(b); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

User does not use PowerShell as their primary shell. plyer provides a clean, testable API
and keeps PowerShell out of the dependency chain. Made optional so the base install stays lean.

---

### D3: Windows shell target — `[APPROVED: (b) Git Bash primary]`

Which Windows shell to target as the primary environment.

#### (a) PowerShell primary

Design for PowerShell as the main Windows shell. All subprocess calls, path quoting, and
script invocations use PowerShell conventions.

**Pros:**

- PowerShell is the modern Windows default
- Better Windows-native integration

**Cons:**

- User does not prefer PowerShell
- Many existing bash scripts would need PowerShell rewrites

#### (b) Git Bash primary

Target Git Bash (MSYS2 environment bundled with Git for Windows) as the primary shell.
PowerShell remains a secondary target where no bash-specific calls are made.

**Pros:**

- User's preferred shell
- Bash scripts work as-is in Git Bash
- `tmux` available via MSYS2 (`pacman -S tmux`)
- `ai c`/`ai g` work without modification

**Cons:**

- Requires Git for Windows to be installed

#### Recommendation

> **Decision:** `APPROVED — (b) Git Bash primary`
<!-- decision-record: chosen-option=(b); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

User uses Git Bash as their primary Windows shell. All bash session scripts work in Git Bash.
tmux is installable via MSYS2. PowerShell compatibility is maintained where it doesn't conflict.

---

### D4: Version bump — `[APPROVED: (b) 0.6.0]`

This is a significant portability change introducing new hard dependencies and CI matrix expansion.

#### (a) Patch bump 0.5.x

**Pros:** conservative; signals no API changes

**Cons:** misleading — two new hard dependencies is a non-trivial change

#### (b) Minor bump 0.6.0

**Pros:** signals meaningful new capability (Windows support); appropriate for new hard deps

**Cons:** none

#### Recommendation

> **Decision:** `APPROVED — (b) 0.6.0`
<!-- decision-record: chosen-option=(b); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

---

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

### 2. `fcntl` → `portalocker` (`iterm2.py`, `quota.py`)

Replace all `fcntl.flock` calls with `portalocker.lock` / `portalocker.unlock`. On Windows,
portalocker uses `msvcrt.locking`; on POSIX it uses `fcntl`. Same semantics, no branching.

```python
import portalocker

with open(lock_path, "w") as f:
    portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
    # ... critical section ...
    portalocker.unlock(f)
```

### 3. `os.kill(pid, 0)` → `psutil` (`tunnel.py`, `sync.py`)

```python
import psutil


def _pid_alive(pid: int) -> bool:
    return psutil.pid_exists(pid)
```

`psutil.pid_exists` is cross-platform and handles edge cases (permission errors, zombie
processes) correctly on all OSes.

### 4. Hardcoded `/tmp`, `/dev/null`, and Console Encoding

| File | Current | Fix |
|------|---------|-----|
| `notifications.py:248` | `f"/tmp/ai-batch-{session_id}.lock"` | `Path(tempfile.gettempdir()) / f"ai-batch-{session_id}.lock"` |
| `gemini.py:1016` | `"/dev/null"` string | `os.devnull` |
| `research.py:546` | `"/dev/null"` string | `os.devnull` |

**Console encoding:** Windows defaults to cp1252, which cannot encode emoji (📊, ✅, etc.) used
in statusline output — this raises `UnicodeEncodeError` at runtime. Fix: add a UTF-8
reconfiguration guard in any module that writes emoji to stdout:

```python
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

Alternatively, document `PYTHONUTF8=1` as a required environment variable for Windows users
in T-08. Both approaches are implemented: code-level guard for robustness, env var in docs.

**`os.replace()` on Windows:** Windows holds exclusive locks on open files — `os.replace()`
over a file that another process has open fails with `PermissionError` (POSIX silently
succeeds). Audit T-02 and T-04 for any `os.replace()` patterns affecting lock or cache
files; use `try/except PermissionError` where needed.

### 5. OS Notifications — plyer (`notifications.py`)

Add a `win32` branch that uses `plyer` when installed:

```python
elif sys.platform == "win32":
    try:
        from plyer import notification as _plyer_notify
        _plyer_notify.notify(title=title, message=body, app_name="ai-cli-utils")
    except ImportError:
        pass  # [notify-win] extra not installed — silently degrade
```

Install: `pip install "ai-cli-utils[notify-win]"`. `pyproject.toml`:

```toml
[project.optional-dependencies]
notify-win = ["plyer>=2.1"]
```

### 6. iTerm2 Guards (`iterm2.py`)

`_is_iterm2()` already returns `False` on non-macOS. The AppleScript call already has a
`sys.platform != "darwin"` guard. The only fix needed is replacing `import fcntl` with
`portalocker` (item 2 above).

### 7. `ai c` / `ai g` on Windows (Git Bash)

`ai c` and `ai g` invoke `tmux` as a subprocess. In Git Bash with tmux installed, this works
without code changes. The bash session scripts run in Git Bash natively.

**Requirement:** tmux must be installed via MSYS2:

```bash
# From Git Bash (MSYS2 pacman)
pacman -S tmux
```

Document this in README and `docs/tools/ai-cli-usage.md`.

### 8. CI Matrix (`.github/workflows/ci.yml`)

```yaml
strategy:
  matrix:
    python-version: ["3.11", "3.12", "3.13"]
    os: [ubuntu-latest, windows-latest, macos-latest]
```

### 9. Test Strategy — No Skipping

Since D1=C (portalocker + psutil replace all POSIX-specific calls), most formerly
platform-specific code paths become cross-platform. Tests run identically everywhere.

For code that truly varies by platform (XDG path helpers, iTerm2 detection):

```python
# Mock sys.platform in tests — no skipif needed
with patch("sys.platform", "win32"):
    assert get_xdg_config_home() == Path.home() / "AppData" / "Roaming" / "ai-cli-utils"
```

iTerm2 tests mock `_is_iterm2()` to return `True` regardless of OS so the logic is tested
on all platforms without requiring an actual macOS+iTerm2 environment.

## Task Breakdown

### T-01: Platform-aware path helpers in `config.py`

**Size:** S
**Batch:** 1

Add `get_xdg_config_home()` and `get_xdg_state_home()` Windows branches using `%APPDATA%`
and `%LOCALAPPDATA%`. All callers already use these helpers.

**Deliverables:**

- `src/ai_cli/config.py` — updated helpers
- `tests/test_config.py` — platform-mocked tests for Windows paths

**Acceptance criteria:**

- [x] `get_xdg_config_home()` returns `%APPDATA%/ai-cli-utils` when `sys.platform == "win32"`
- [x] `get_xdg_state_home()` returns `%LOCALAPPDATA%/ai-cli-utils` when `sys.platform == "win32"`
- [x] Both functions return XDG paths on Linux/macOS (unchanged behavior)
- [x] Tests mock `sys.platform` — no platform guard on the tests themselves

**Dependencies:** None

---

### T-02: Replace `fcntl` with `portalocker` (`iterm2.py`, `quota.py`)

**Size:** S
**Batch:** 1

Replace all `fcntl.flock` usages with `portalocker`. Add `portalocker` to `pyproject.toml`
hard dependencies.

**Deliverables:**

- `src/ai_cli/iterm2.py` — `fcntl` → `portalocker`
- `src/ai_cli/quota.py` — any `fcntl` usage → `portalocker`
- `pyproject.toml` — `portalocker>=4.0` added to dependencies
- `tests/` — verify locking tests pass on CI

**Acceptance criteria:**

- [x] `import ai_cli` succeeds on Windows (no `fcntl` import at module level)
- [x] Lock file behavior unchanged on macOS/Linux
- [x] All existing locking tests pass

**Dependencies:** None

---

### T-03: Replace `/tmp`, `/dev/null` hardcodes, and fix console encoding

**Size:** S
**Batch:** 1

**Deliverables:**

- `src/ai_cli/notifications.py` — `/tmp/...` → `tempfile.gettempdir()`
- `src/ai_cli/gemini.py` — `"/dev/null"` → `os.devnull`
- `src/ai_cli/research.py` — `"/dev/null"` → `os.devnull`
- `src/ai_cli/quota.py` — add UTF-8 stdout reconfigure guard for emoji output on Windows
- Audit all modules printing emoji/Unicode for `sys.stdout.reconfigure` guard

**Acceptance criteria:**

- [x] `tempfile.gettempdir()` used for all temp paths
- [x] `os.devnull` used for all null-device references
- [x] `quota_statusline_part()` does not raise `UnicodeEncodeError` on Windows (emoji output safe)
- [x] All existing tests pass

**Dependencies:** None

---

### T-04: Portable `_pid_alive()` with `psutil`

**Size:** S
**Batch:** 2

Add `psutil` to hard dependencies. Replace all `os.kill(pid, 0)` calls with
`psutil.pid_exists(pid)`. Extract shared `_pid_alive()` helper into `config.py` or a new
`src/ai_cli/process.py`.

**Deliverables:**

- `src/ai_cli/tunnel.py` — `os.kill(pid, 0)` → `psutil.pid_exists`
- `src/ai_cli/sync.py` — `os.kill(pid, 0)` → `psutil.pid_exists`
- `pyproject.toml` — `psutil>=5.9` added to dependencies
- `tests/` — updated process-existence tests (mock `psutil.pid_exists`)

**Acceptance criteria:**

- [x] `_pid_alive(pid)` returns `True` for a live PID, `False` otherwise on all platforms
- [x] No `os.kill(pid, 0)` calls remain in the codebase
- [x] All existing tests pass

**Dependencies:** T-01

---

### T-05: Windows OS notification branch (`notifications.py`)

**Size:** S
**Batch:** 2

Add `win32` branch in `_send_os_notification()` using `plyer`. Add `[notify-win]` optional
extra to `pyproject.toml`.

**Deliverables:**

- `src/ai_cli/notifications.py` — Windows branch using plyer
- `pyproject.toml` — `[project.optional-dependencies] notify-win = ["plyer>=2.1"]`
- `tests/test_notifications.py` — Windows branch test (mock `sys.platform`, mock plyer import)

**Acceptance criteria:**

- [x] `_send_os_notification()` calls `plyer.notification.notify()` when `sys.platform == "win32"` and plyer is installed
- [x] Silently degrades (no exception) when plyer is not installed
- [x] Base install (`pip install ai-cli-utils`) does not pull in plyer

**Dependencies:** T-01

---

### T-06: CI matrix expansion to `windows-latest`

**Size:** S
**Batch:** 3

Add `windows-latest` to the CI OS matrix. Verify all tests pass on Windows CI.

**Deliverables:**

- `.github/workflows/ci.yml` — `os: [ubuntu-latest, windows-latest, macos-latest]`

**Acceptance criteria:**

- [x] CI passes on `windows-latest` for Python 3.11, 3.12, 3.13
- [x] No test skips on Windows (all tests run and pass)

**Dependencies:** T-01–T-05

---

### T-07: Test cross-platform coverage

**Size:** M
**Batch:** 3

Audit all tests that use platform-specific assumptions. Replace `skipif win32` guards with
`patch("sys.platform", "win32")` mocks. Ensure iTerm2 tests mock `_is_iterm2()` so they
run on all platforms.

**Deliverables:**

- `tests/` — all platform-specific tests updated to mock rather than skip

**Acceptance criteria:**

- [x] Zero `pytest.mark.skipif(sys.platform == "win32", ...)` decorators in test suite
- [x] All tests pass on macOS, Linux, and Windows CI

**Dependencies:** T-01–T-05

---

### T-08: Docs — document Windows requirements and unsupported features

**Size:** S
**Batch:** 3

**Deliverables:**

- `README.md` — add Windows installation section
- `docs/tools/ai-cli-usage.md` — note `ai c`/`ai g` require tmux via MSYS2; note signal-watch
  is unsupported on Windows

**Acceptance criteria:**

- [x] README explains `pip install ai-cli-utils` on Windows and Git Bash + MSYS2 setup
- [x] `ai c`/`ai g` tmux requirement documented
- [x] Unsupported features listed with clear rationale

**Dependencies:** T-06

---

### T-09: Document `ai c`/`ai g` tmux requirement for Windows

**Size:** XS
**Batch:** 1

Add a `sys.platform == "win32"` guard at the top of `_do_session_launch` that prints a
helpful error if tmux is not found, rather than crashing with a cryptic subprocess error.

**Deliverables:**

- `src/ai_cli/main.py` — graceful "tmux not found — install via MSYS2: `pacman -S tmux`" message if `tmux` is not on PATH on Windows

**Acceptance criteria:**

- [x] `ai c 1` on Windows without tmux prints a clear error message
- [x] `ai c 1` on Windows with tmux installed works normally

**Dependencies:** T-01

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03, T-09 | Unblock `import ai_cli` on Windows; fix paths, null devices, console encoding | — (automated: CI) |
| 2 | T-04, T-05 | Process checks and notifications | — |
| 3 | T-06, T-07, T-08 | CI matrix, test audit, docs | Automated: CI green on `windows-latest` |

> **Feedback Round 1:** Batching confirmed. T-09 added to Batch 1 (graceful tmux-not-found message).
>
> **Feedback Round 2:** Batch 1 human gate removed. Gemini Flash research (2026-04-25) confirmed
> `pip install ai-cli-utils` works on Windows with no caveats beyond the POSIX fixes already planned:
> `portalocker` is a universal wheel (no compiler), `psutil` ships `cp37-abi3-win_amd64.whl`
> covering Python 3.7–3.13+, `plyer` is pure Python. CI on `windows-latest` (T-06) serves as
> automated verification. Additional Windows issues surfaced: console encoding (cp1252 breaks emoji),
> `os.replace()` semantics on open files — both addressed in T-03 and the Technical Design section.
> Implementation is fully autonomous; no human gates between batches.

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before any code | Approve scope and approach — **DONE** |
| ~~Batch 1 UAT~~ | ~~After Batch 1~~ | ~~Removed — pip install confirmed working via Gemini Flash research~~ |
| CI green on `windows-latest` | After T-06 lands | Automated gate — CI must pass on `windows-latest` before closing AI-CLI-29 |

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
| 2026-04-25 | Round 1 | D1=C (portalocker+psutil hard deps). D2=plyer optional [notify-win] (no PowerShell). D3=Git Bash primary. D4=0.6.0 minor bump. ai c/ai g moved in scope — work in Git Bash with tmux via MSYS2. No test skipping — mock sys.platform instead. Status: DRAFT → APPROVED. |
| 2026-04-25 | Round 2 | Batch 1 human gate removed — Gemini Flash research confirmed pip install works on Windows with all deps as pure-Python or pre-built wheels. Autonomous implementation approved for all 9 tasks. Two additional in-scope items added: console encoding (T-03) and os.replace() audit (T-02/T-04). |
| 2026-04-25 | Complete | All 9 tasks (T-01–T-09) implemented and merged. 1767 tests passing (2 skipped). Version bumped to 0.6.0. CI matrix expanded to include windows-latest. All ACs self-reported as verified. |
| 2026-04-25 | Audit complete | Independent audit of all T-01–T-09 ACs against src/ai_cli/. All ACs verified: Windows XDG paths, portalocker (no fcntl), os.devnull/tempfile, psutil `_pid_alive()`, plyer notify-win, CI windows-latest, no skipif win32, README Windows section, graceful tmux-not-found. Full test suite: 1781 passed, 2 skipped. |
