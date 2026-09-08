---
title: "`ai ls` and `ai attach` crash on Windows — bare tmux calls, tmux unavailable on Windows"
category: bug
tags: [bug, windows, tmux, subprocess, platform-compatibility]
status: open
source: "CORE-6nr"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# [CORE-6nr] `ai ls` and `ai attach` crash on Windows — bare `tmux` calls, tmux unavailable on Windows

**Status:** open — found during CORE-5m6-ai-cli-bare-uv-popen-windows-crash audit, NOT fixed

**Severity:** P2

**Created:** 2026-07-31

**Task:** CORE-6nr (filed in core-cli store; ai-cli-utils store blocked by CORE-5dx)

**Related issues:**
- CORE-5m6-ai-cli-bare-uv-popen-windows-crash (fixed; same `FileNotFoundError` class, different tool)

## Summary

`ai ls` and `ai attach <session>` crash on Windows with `FileNotFoundError: [WinError 2] The system cannot find the file specified`. Root cause: two functions (`_do_ls()` ~line 1460, `_do_attach()` ~line 1452) call bare `"tmux"` in `subprocess.run()` and `os.execvp()` without resolving the path. This is the same structural defect as CORE-5m6-ai-cli-bare-uv-popen-windows-crash (bare subprocess names are not portable on Windows), but with a deeper platform incompatibility: **tmux does not exist on Windows** — it is a POSIX-only terminal multiplexer with no native Windows port.

The correct fix is not "resolve the path" (as in the uv bug) but "gate these commands behind a platform check" or "document as unsupported on Windows." This issue is filed separately to capture the design decision: do these commands fail gracefully with a clear "tmux is not available on Windows" message, or are they silently no-ops on Windows, or is the whole session-management feature Windows-unsupported?

**NOT FIXED** in the uv bug fix — left as a separate, open issue for explicit design decision.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

**Environment:**
- Windows 11 Enterprise 10.0.26100 (BMS corporate machine)
- Git Bash (MINGW64)
- `ai-cli-utils` installed editable
- tmux not installed (and unavailable for Windows)

**1. `ai ls` crash (expected):**
```bash
$ ai ls
Traceback (most recent call last):
  File "...\ai_cli\main.py", line 1461, in _do_ls
    res = subprocess.run(
          ^^^^^^^^^^^^^^^
  File "...\subprocess.py", line 550, in run
    with Popen(*popenargs, **kwargs) as process:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "...\subprocess.py", line 1077, in __init__
    self._execute_child(args, executable, preexec_fn, close_fds,
  File "...\subprocess.py", line 1538, in _execute_child
    hp, ht, pid, tid = _winapi.CreateProcess(executable, args,
                       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
FileNotFoundError: [WinError 2] The system cannot find the file specified
```

**2. `ai attach <session>` crash (expected):**
```bash
$ ai attach my-session
Traceback (most recent call last):
  File "...\ai_cli\main.py", line 1453, in _do_attach
    check = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "...\subprocess.py", line 550, in run
    with Popen(*popenargs, **kwargs) as process:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "...\subprocess.py", line 1077, in __init__
    self._execute_child(args, executable, preexec_fn, close_fds,
  File "...\subprocess.py", line 1538, in _execute_child
    hp, ht, pid, tid = _winapi.CreateProcess(executable, args,
                       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
FileNotFoundError: [WinError 2] The system cannot find the file specified
```

**3. Defective code (current, two sites):**

**Site A: `_do_ls()` ~line 1460**
```python
def _do_ls(show_all: bool) -> None:
    res = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print("No tmux sessions found (is tmux running?)", file=sys.stderr)
        sys.exit(0)
    # ... parse and display sessions
```

**Site B: `_do_attach()` ~line 1452**
```python
def _do_attach(session_name: str) -> None:
    check = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
    if check.returncode != 0:
        print(f"No tmux session named '{session_name}'", file=sys.stderr)
        sys.exit(1)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])
```

Both pass bare `"tmux"` without checking platform or attempting path resolution.

**4. Context:** These functions are called by `ai ls` and `ai attach` CLI commands, which are intended for managing tmux sessions created by Claude Code / Gemini sessions. On macOS/Linux, tmux is available and the commands work. On Windows, tmux does not exist, and the bare name crashes.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause Analysis

**Causal chain:**

1. **tmux is POSIX-only.** There is no native Windows port of tmux. Windows users typically use Windows Terminal, ConEmu, or other terminal emulators, none of which are tmux-compatible.

2. **Bare subprocess names are not portable on Windows** (same as CORE-5m6-ai-cli-bare-uv-popen-windows-crash). Even if tmux were available, passing `"tmux"` to `subprocess.run()` without a shell would fail on Windows unless the path included a directory separator.

3. **No platform guard.** The code does not check `sys.platform` or attempt to detect tmux availability before calling these functions. The crash happens at the subprocess call, not at a graceful check-and-fail point.

4. **The error message is misleading.** `"No tmux sessions found (is tmux running?)"` in `_do_ls()` suggests tmux is present but not running, when the actual problem on Windows is tmux's complete absence.

**Design questions (not resolved here):**

- Should `ai ls` / `ai attach` be gated behind a platform check (`if sys.platform != "win32"`) and fail with a clear message on Windows?
- Should these commands silently no-op on Windows (no crash, no sessions listed)?
- Should the whole session-management feature be documented as macOS/Linux-only?
- Are there Windows equivalents (e.g., Windows Terminal session management) that could be supported in the future?

This issue captures the defect but defers the design decision to a future fix.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="options" kind="replaceable" -->

## Fix Options

**Option A: Graceful platform check (recommended)**

```python
def _do_ls(show_all: bool) -> None:
    if sys.platform == "win32":
        print("Error: tmux session management is not available on Windows.", file=sys.stderr)
        print("tmux is a POSIX-only terminal multiplexer with no native Windows port.", file=sys.stderr)
        sys.exit(1)
    # ... existing tmux logic
```

Same check in `_do_attach()`. Clear, honest failure with actionable message.

**Option B: Silent no-op on Windows**

```python
def _do_ls(show_all: bool) -> None:
    if sys.platform == "win32":
        print("No sessions found.", file=sys.stderr)
        sys.exit(0)
    # ... existing tmux logic
```

Less honest (implies sessions are supported but none exist), but avoids crashes.

**Option C: Resolve the path and fail gracefully if tmux is not found**

```python
def _do_ls(show_all: bool) -> None:
    tmux_bin = shutil.which("tmux")
    if not tmux_bin:
        print("Error: tmux not found on PATH.", file=sys.stderr)
        print("Session management requires tmux (available on macOS/Linux).", file=sys.stderr)
        sys.exit(1)
    res = subprocess.run(
        [tmux_bin, "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
    )
    # ... rest of logic
```

This is the minimal fix (same pattern as the uv bug), but it doesn't address the deeper question of whether tmux-based session management is even intended for Windows users.

**Option D: Document as unsupported**

Add to `ai --help` or README: "`ai ls` and `ai attach` require tmux and are only supported on macOS/Linux."

No code change, just documentation. Crashes remain, but expectations are set.

**Recommendation:** Option A (graceful platform check) or Option C (resolve path + clear message) are the most user-friendly. Option D (document-only) is acceptable if the feature is explicitly scoped to POSIX.

<!-- /doc:region name="options" -->

<!-- doc:region name="lessons" kind="replaceable" -->

## Lessons Learned

1. **Platform-specific tools need explicit guards.** Assuming a tool is present (tmux, pbcopy, open) without checking `sys.platform` or `shutil.which()` creates hard crashes on unsupported platforms.

2. **Error messages should match the actual failure.** `"No tmux sessions found (is tmux running?)"` is misleading on Windows — the problem is not tmux's state, but its absence.

3. **The public open-source package standard requires portability.** ai-cli-utils `CLAUDE.md` states: "OS portability — all code must account for Windows, macOS, and Linux differences. No macOS-only assumptions." This is a standing violation of that rule.

4. **Bare subprocess names are a recurring anti-pattern on Windows.** This is the third instance found (after the two uv sites). A grep audit of all `subprocess.run()` / `subprocess.Popen()` / `os.execvp()` calls for bare tool names may reveal more.

<!-- /doc:region name="lessons" -->

<!-- doc:region name="appendix" kind="replaceable" -->

## Appendix: Relationship to CORE-5m6-ai-cli-bare-uv-popen-windows-crash

Both bugs share the same `FileNotFoundError` symptom and the same root cause class (bare subprocess names on Windows), but they require different fixes:

- **uv bug (fixed):** Tool is present and cross-platform; fix is "resolve the path with `shutil.which()` and pass the absolute path."
- **tmux bug (open):** Tool is POSIX-only and unavailable on Windows; fix is "gate behind a platform check or document as unsupported."

This is why the tmux bug was NOT fixed during the uv bug fix — it requires a design decision about the intended scope of the session-management feature, not just a mechanical path-resolution change.

**Paper trail:**
- Found during audit of CORE-5m6-ai-cli-bare-uv-popen-windows-crash
- Filed separately to capture the design question
- ai-cli-utils is installed editable; no commit per BMS machine git discipline
- Real beads issue ID will be backfilled once the Windows/Dolt blocker is resolved

<!-- /doc:region name="appendix" -->
