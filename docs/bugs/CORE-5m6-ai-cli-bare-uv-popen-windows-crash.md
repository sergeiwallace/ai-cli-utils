---
title: "Bare `uv` subprocess call crashes on Windows — shutil.which() result discarded, CreateProcess cannot resolve bare names"
category: bug
tags: [bug, windows, subprocess, uv, path-resolution, background-update, upgrade]
status: resolved
source: "CORE-5m6"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# [CORE-5m6] Bare `uv` subprocess call crashes on Windows — `shutil.which()` result discarded, CreateProcess cannot resolve bare names

**Status:** resolved — fix applied and verified 2026-07-31

**Severity:** P1

**Created:** 2026-07-31

**Task:** CORE-5m6 (filed in ai-core store; ai-cli-utils store blocked by CORE-5dx)

**Fixed by:** `src/ai_cli/main.py` — two sites, commit not created (per BMS machine git discipline)

**Related issues:**
- CORE-6nr (separate, pre-existing Windows incompatibility — `ai ls` crashes with identical `FileNotFoundError` class for a different bare-name call; NOT fixed here, filed separately)

**Related docs:**
- ai-harness `docs/procedures/beads-work-machine-install.md` — documents Windows/Dolt known issues
- ai-harness `docs/research/beads-cross-machine-sync.md` — Open bug #4770 (Windows 11 `bd dolt pull` hang)

## Summary

`ai c 1` (or any other `ai` command) crashed on Windows 11 with `FileNotFoundError: [WinError 2] The system cannot find the file specified` from `subprocess.Popen` → `_winapi.CreateProcess`. Root cause: two sites in `src/ai_cli/main.py` correctly called `shutil.which("uv")` to resolve uv's absolute path, then **discarded the result** and passed the bare string `"uv"` to a `shell=False` subprocess call. Windows `CreateProcess`/`execvp` cannot resolve a bare name without a shell — it requires an absolute path or a path containing a directory separator.

The bug surfaced because the user's VS Code integrated terminal held a stale environment block predating uv's installation, so `shutil.which("uv")` returned `None` — triggering the latent bare-name defect. On a fresh shell with uv on PATH, the same code would still be structurally wrong (passing a bare name that the OS cannot resolve), but the `None` case forced immediate failure and made the bug visible.

**Two distinct sites with deliberately asymmetric error semantics:**

1. **`trigger_background_update()` (~line 449)** — An unrequested background check firing during another command. Must NOT raise: raising would kill the foreground command the user actually asked for. Fix: warn on stderr and return. `Popen` wrapped in `try/except OSError` that reports but never propagates.

2. **`cmd_upgrade()` (~line 2248)** — The user explicitly invoked `ai upgrade`. Must fail LOUDLY: silence would be a lie. Fix: message to stderr + `sys.exit(1)`.

The asymmetry is the interesting part and is documented here as design rationale: a background update check is opportunistic (skip-on-failure is correct), while a user-invoked upgrade is a request (must honor or report failure).

Silent-skip was rejected for the background case because it would hide a permanently broken auto-updater with no signal, hence the stderr warning.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

**Environment:**
- Windows 11 Enterprise 10.0.26100 (BMS corporate machine, `bms-windows`)
- Git Bash (MINGW64), bash 5.2.37
- `ai-cli-utils` installed editable via `uv tool install --editable ~/projects/ai-cli-utils` — source edits are live immediately, no reinstall required
- VS Code integrated terminal with stale environment block (opened before uv installation, `which uv` → not found)

**1. Pre-fix crash:**
```bash
$ ai c 1
Traceback (most recent call last):
  File "C:\Users\<user>\AppData\Local\Programs\Python\Python313\Lib\site-packages\ai_cli\main.py", line 466, in trigger_background_update
    subprocess.Popen(
  File "C:\Users\<user>\AppData\Local\Programs\Python\Python313\Lib\subprocess.py", line 1077, in __init__
    self._execute_child(args, executable, preexec_fn, close_fds,
    ~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         pass_fds, cwd, env, startupinfo,
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         creationflags, shell, p2cread, p2cwrite,
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         c2pread, c2pwrite, errread, errwrite,
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                         restore_signals, start_new_session)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\<user>\AppData\Local\Programs\Python\Python313\Lib\subprocess.py", line 1538, in _execute_child
    hp, ht, pid, tid = _winapi.CreateProcess(executable, args,
                       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
FileNotFoundError: [WinError 2] The system cannot find the file specified
```

**2. Contributing condition (stale PATH):**
```bash
$ which uv
# (no output — uv not on PATH in this shell)
$ uv --version  # from a fresh PowerShell in the same session
uv 0.11.32 (installed)
```

A fresh git bash shell with uv on PATH would not crash immediately, but the bare-name call is still structurally wrong — Windows `CreateProcess` requires an absolute path or a path with a directory separator for `shell=False` subprocess calls.

**3. Defective code (pre-fix, two sites):**

**Site A: `trigger_background_update()` ~line 449**
```python
uv_bin = shutil.which("uv")  # correctly resolves to absolute path or None
if not uv_bin:
    # Pre-fix: no check here — fell through to Popen with uv_bin=None
    pass
# Pre-fix: passed bare "uv" instead of uv_bin
upgrade_cmd = ["uv", "tool", "upgrade", "ai-cli-utils"]
if _should_use_uv_link_mode_copy(uv_bin):  # used uv_bin here but not in upgrade_cmd
    upgrade_cmd.append("--link-mode=copy")
subprocess.Popen(
    upgrade_cmd,  # bare "uv" — Windows CreateProcess cannot resolve this
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
```

**Site B: `cmd_upgrade()` ~line 2248**
```python
uv_bin = shutil.which("uv")  # correctly resolves
if not uv_bin:
    # Pre-fix: no check here either — fell through to execvp with uv_bin=None
    pass
# Pre-fix: passed bare "uv" in both the target and args
upgrade_args = ["uv", "tool", "upgrade", "ai-cli-utils"]
if _should_use_uv_link_mode_copy(uv_bin):  # used uv_bin here but not in execvp
    upgrade_args.append("--link-mode=copy")
os.execvp("uv", upgrade_args)  # bare "uv" in both args — same class of failure
```

**4. Post-fix verification (with uv hidden from PATH):**
```bash
$ export PATH=$(echo $PATH | sed 's|[^:]*\.local/bin[^:]*:||g')  # remove uv from PATH
$ which uv
# (no output)
$ ai c --help
Warning: 'uv' not found on PATH — skipping background update check.
# (normal help output follows, exit 0)
$ ai upgrade
Upgrading ai-cli-utils...
Cannot find 'uv' on PATH — unable to upgrade. Install uv, or add its
directory to PATH, then re-run 'ai upgrade'.
$ echo $?
1
```

Both sites now handle the `None` case correctly and asymmetrically: background check warns but returns normally (foreground command succeeds), explicit upgrade fails loudly with exit 1.

**5. Post-fix verification (with uv on PATH):**
```bash
$ export PATH="$HOME/.local/bin:$PATH"
$ which uv
/c/Users/<user>/.local/bin/uv
$ ai c --help
# (no warning, normal help output, exit 0 — background check runs silently)
$ ai upgrade
Upgrading ai-cli-utils...
# (uv tool upgrade executes, replacing the process)
```

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause Analysis

**Causal chain:**

1. **Windows `CreateProcess`/`execvp` semantics differ from POSIX `execvp`.** On POSIX, `execvp("uv", ...)` searches PATH automatically. On Windows, without a shell, the executable argument must be an absolute path or a relative path containing a directory separator (e.g., `./uv.exe`) — a bare name like `"uv"` cannot be resolved.

2. **Both sites correctly called `shutil.which("uv")`** to get the absolute path but then **discarded the result** and hardcoded the bare string `"uv"` in the actual subprocess call. This is a code structure defect, not a Windows-specific oversight — the resolution was performed and thrown away.

3. **The `None` case was not guarded.** When `shutil.which("uv")` returned `None` (uv not on PATH), the code fell through to the subprocess call with no early-exit path. On Windows this raised immediately because `CreateProcess` cannot resolve `None` or a bare name.

4. **Contributing condition: stale VS Code environment.** The user's integrated terminal was opened before uv was installed, so its `PATH` did not include `~/.local/bin/`. A fresh shell would have uv on PATH, and `shutil.which("uv")` would return a valid path — but the bare-name call is still structurally wrong even in that case, because the code passes `"uv"` (bare string) instead of `uv_bin` (resolved absolute path).

5. **Cross-platform portability violation.** The public open-source package standards (ai-cli-utils `CLAUDE.md`) require: "OS portability — all code must account for Windows, macOS, and Linux differences. No macOS-only assumptions." This was a macOS/Linux assumption (bare names resolve via `execvp`) that broke on Windows.

**Why the two sites have different error semantics:**

- **`trigger_background_update()`** fires opportunistically during any `ai` command (every 24h). It is NOT a user request. Raising an exception here kills the foreground command the user actually asked for (e.g., `ai c 1`). The correct behavior is to skip the update check and let the foreground command proceed — hence warn-and-return, not raise. Silent-skip was rejected because a permanently broken updater should signal, not hide.

- **`cmd_upgrade()`** is a user-invoked command (`ai upgrade`). Silence would be a lie: the user asked to upgrade, and it cannot. Failing loudly (message + exit 1) is the only honest response.

**Sites audited and found correct (no change needed):**

- **Line ~429** — `ai_bin` (already used the resolved var from `shutil.which("ai")` or explicit fallback)
- **Line ~1329** — `uv_bin` with sensible fallback: `uv_bin = shutil.which("uv") or "uv"` — this was already passing the resolved path when available

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix" kind="replaceable" -->

## Fix

**Site A: `trigger_background_update()` ~line 449-474**

```python
uv_bin = shutil.which("uv")
if not uv_bin:
    # Warn but do not raise. This is an unrequested background check firing during
    # some other command, so a hard failure here would kill the foreground command
    # the user actually asked for (the AIH bug: bare "uv" + Popen without a shell
    # -> Windows CreateProcess raises FileNotFoundError -> `ai c 1` traceback).
    # Silent-skip would hide a permanently broken auto-updater, so warn on stderr.
    print(
        "Warning: 'uv' not found on PATH — skipping background update check.",
        file=sys.stderr,
    )
    return
# Pass the resolved absolute path, not the bare name — see above.
upgrade_cmd = [uv_bin, "tool", "upgrade", "ai-cli-utils"]
if _should_use_uv_link_mode_copy(uv_bin):
    upgrade_cmd.append("--link-mode=copy")
try:
    subprocess.Popen(
        upgrade_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
except OSError as exc:
    # Same reasoning: report, never propagate, so the foreground command survives.
    print(f"Warning: background update check failed to launch: {exc}", file=sys.stderr)
```

**Site B: `cmd_upgrade()` ~line 2248-2264**

```python
@_cli_group.command("upgrade", help="Upgrade ai-cli-utils via uv tool upgrade")
def cmd_upgrade():
    print("Upgrading ai-cli-utils...", file=sys.stderr)
    uv_bin = shutil.which("uv")
    if not uv_bin:
        print(
            "Cannot find 'uv' on PATH — unable to upgrade. Install uv, or add its\n"
            "directory to PATH, then re-run 'ai upgrade'.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Pass the resolved absolute path, not the bare name: on Windows a bare name is
    # not resolvable by CreateProcess/execvp and raises FileNotFoundError.
    upgrade_args = [uv_bin, "tool", "upgrade", "ai-cli-utils"]
    if _should_use_uv_link_mode_copy(uv_bin):
        upgrade_args.append("--link-mode=copy")
    os.execvp(uv_bin, upgrade_args)
```

**Verification:**

With uv removed from PATH (simulating the stale-shell condition):
- `ai c --help` → warning on stderr, normal help output, exit 0
- `ai upgrade` → error message, exit 1

With uv on PATH:
- `ai c --help` → no warning, exit 0
- `ai upgrade` → executes `uv tool upgrade`, replaces process

Tested 2026-07-31 on Windows 11 Enterprise, git bash, with editable install (changes live immediately).

<!-- /doc:region name="fix" -->

<!-- doc:region name="lessons" kind="replaceable" -->

## Lessons Learned

1. **Bare subprocess names are not portable.** Even when `shutil.which()` succeeds and returns a path, always pass that resolved path to the subprocess call, not a bare name. Windows `CreateProcess` (and POSIX `execve`, which `execvp` wraps) require explicit paths.

2. **Opportunistic background operations must never crash the foreground.** `trigger_background_update()` is called during every `ai` command (24h throttle) — if it raises, the user's command dies. The fix: warn on stderr (signal the problem) but return normally (let the foreground command proceed).

3. **User-invoked operations must fail loudly.** `ai upgrade` is a request; silence or a no-op would be misleading. The fix: clear error message + non-zero exit.

4. **Test on Windows early.** The defect existed on all platforms (bare names were always passed), but only Windows surfaced it immediately. POSIX `execvp` silently compensated by searching PATH, masking the structural bug until a Windows user hit it.

5. **Editable installs are a fast fix-test loop.** `uv tool install --editable` made the fix immediately live without reinstall, enabling rapid verification of both the crash and the fix.

6. **Stale VS Code environment blocks are a common Windows trap.** Integrated terminals opened before a tool installation inherit the old PATH. The fix must handle `shutil.which()` returning `None`, not assume the tool is present just because it was installed earlier in the session.

<!-- /doc:region name="lessons" -->

<!-- doc:region name="related_issues" kind="replaceable" -->

## Related Issues

**CORE-6nr** (separate, NOT fixed here):

During the audit of bare subprocess calls, a THIRD, PRE-EXISTING gap was found:

- **`_do_ls()` ~line 1460** and **`_do_attach()` ~line 1452** call bare `"tmux"` in subprocess.run/os.execvp
- `ai ls` crashes on Windows with the same `FileNotFoundError` class: `[WinError 2] The system cannot find the file specified`
- Root cause: identical to this bug (bare name, no shell, Windows CreateProcess cannot resolve)
- **NOT fixed in this change.** Filed as a separate beads issue (CORE-6nr) and mentioned here only for completeness. Fixing it requires a broader design decision: tmux does not exist on Windows; the correct fix may be "gate these commands behind a platform check" or "document Windows-unsupported" rather than "resolve the path."

**ai-harness beads Windows/Dolt issues** (blocker for filing this bug):

- Both `ai-cli-utils` and `ai-harness` beads stores failed to initialize on this Windows 11 machine
- `bd init --prefix <PREFIX> --skip-agents --skip-hooks --non-interactive` completes (exit 0) but leaves the Dolt database corrupted: `.beads/embeddeddolt/<PREFIX>/.dolt/` exists but is missing `repo_state.json`
- Subsequent `bd config set` / `bd list` commands fail: `Error 1105: open C:\...\repo_state.json: The system cannot find the file specified.`
- Documented in ai-harness `docs/research/beads-cross-machine-sync.md` § 5, Open bug #4770 (2026-07-14): Windows 11 `bd dolt pull` hangs forever due to pipe deadlock in Dolt's git blobstore
- This is a known Windows/Dolt limitation, not an ai-cli-utils bug. Reported here as context for why this bug and CORE-6nr are filed in the ai-core store rather than the ai-cli-utils one.

<!-- /doc:region name="related_issues" -->

<!-- doc:region name="appendix" kind="replaceable" -->

## Appendix: Code Audit Summary

**All `shutil.which()` calls in `src/ai_cli/main.py` (2026-07-31):**

| Line | Call | Usage | Status |
|------|------|-------|--------|
| ~429 | `ai_bin = shutil.which("ai")` | Correctly passed to subprocess or used explicit fallback | ✓ OK (audited, no change) |
| ~449 | `uv_bin = shutil.which("uv")` | **Bug site A:** discarded result, passed bare `"uv"` to `Popen` | ✗ FIXED |
| ~1329 | `uv_bin = shutil.which("uv") or "uv"` | Correctly passed resolved path in subprocess call | ✓ OK (audited, no change) |
| ~1448 | bare `"tmux"` (no `shutil.which`) | Related issue (NOT fixed here) | ⚠ SEPARATE ISSUE |
| ~1456 | bare `"tmux"` (no `shutil.which`) | Related issue (NOT fixed here) | ⚠ SEPARATE ISSUE |
| ~2248 | `uv_bin = shutil.which("uv")` | **Bug site B:** discarded result, passed bare `"uv"` to `os.execvp` | ✗ FIXED |

**Paper trail:**
- This bug doc serves as the canonical record of the fix
- ai-cli-utils is installed editable; changes are live immediately, no commit required per BMS machine git discipline (ai-harness `CLAUDE.md`: "on the ACN Windows machine follow enterprise constraints (no autonomous push)")
- Real beads issue ID will be backfilled once the Windows/Dolt blocker is resolved or a workaround is found

<!-- /doc:region name="appendix" -->
