---
title: "Windows: ai c hard-exits when tmux is absent instead of falling back to bare mode"
category: bug
tags: [bug, windows, tmux, session-launch]
status: resolved
template_version: "bug-1.0.0"
---

<!-- aido:region name="summary" kind="replaceable" -->

# [AI-CLI-vs8] Windows: ai c hard-exits when tmux is absent instead of falling back to bare mode

**Status:** resolved

**Severity:** P1

**Created:** 2026-07-13

**Task:** AI-CLI-vs8

**Fixed by:** see commit in this PR

---

## Symptoms

Running `ai c <n>` on Windows (Git Bash / MINGW64) with no tmux on PATH exits
immediately with code 1 and the error:

```
Error: tmux not found, and it is required for the default session mode.
  pacman -S tmux            (MSYS2)

Or run without tmux:
  ai <engine> -b            one-off bare launch (no tmux)
  [session] use_tmux = false     in ~/.config/ai-cli-utils/config.toml
                            to make bare the default on this machine
```

The session never launches. The user must either install tmux via MSYS2 or
manually edit `config.toml` before the tool is usable on Windows at all.

## Environment

- OS: Windows 11 (acn-windows, Git Bash / MINGW64)
- `ai-cli-utils` v0.7.0 (editable install)
- tmux: not on PATH (not installed; standard on Windows)

## Reproduction Steps

1. Install `ai-cli-utils` on a Windows machine without tmux.
2. Run `ai c 1` (or any `ai c <n>` invocation).
3. Observe: hard exit with "Error: tmux not found" and code 1.

## Root Cause Analysis

`_do_session_launch` in `src/ai_cli/main.py` (line ~1544) contains this guard:

```python
if not bare and not shutil.which("tmux"):
    if sys.platform == "win32":
        _hint = "  pacman -S tmux            (MSYS2)"
    ...
    print("Error: tmux not found ...", file=sys.stderr)
    sys.exit(1)
```

The platform check only selects the install hint; it does **not** skip the
`sys.exit(1)`. On Windows, where tmux is absent, the guard always fires and
aborts the launch.

The fix was already suggested in the guard's own error message:
`use_tmux = false` in config is described as "a fine permanent choice" for a
machine that "only runs sessions in a local terminal" — which describes every
Windows user. The guard should have set `bare = True` on Windows instead of
exiting.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-04-27 | Windows support pass (`AI-CLI-29`, v0.6.0) added platform-specific hint | Only changed the error message, not the exit behavior |

## Fix

In `_do_session_launch` (`src/ai_cli/main.py`), split the guard so Windows
falls back to bare mode instead of aborting:

```python
if not bare and not shutil.which("tmux"):
    if sys.platform == "win32":
        # tmux is not standard on Windows; bare mode is the correct default.
        # Set [session] use_tmux = false in config.toml to suppress this notice.
        bare = True
    else:
        if sys.platform == "darwin":
            _hint = "  brew install tmux"
        else:
            _hint = "  sudo apt install tmux     ..."
        print("Error: tmux not found ...", file=sys.stderr)
        sys.exit(1)
```

macOS and Linux behavior is unchanged — they still error if tmux is absent,
since those users can and should install it.

## Verification

Regression test frozen in
`tests/test_main.py::TestDoSessionLaunchTmuxGuard::test_when_win32_and_tmux_not_found_then_falls_back_to_bare_mode`.

- [x] New regression test (`test_when_win32_and_tmux_not_found_then_falls_back_to_bare_mode`) confirmed RED on unfixed code
- [x] Test is GREEN after fix
- [x] All 5 `TestDoSessionLaunchTmuxGuard` tests pass (including macOS/Linux behavior unchanged)
- [x] `ruff check src/ tests/` clean
- [x] `tests/test_main.py tests/test_config.py tests/test_sync.py`: 391 passed, 7 skipped, 6 pre-existing failures (AI-CLI-99, POSIX path encoding — unrelated)
- [x] Manual verification: `ai c 2` on acn-windows no longer errors; session launches in bare mode

## Lessons Learned

When adding platform-specific error hints to a guard, always check whether the
guard itself should behave differently on that platform — not just whether the
message should differ. The hint text and the exit path are separate concerns.

<!-- /aido:region name="summary" -->
