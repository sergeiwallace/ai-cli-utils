---
title: "Windows: self-update destroys its own uv tool environment, then reports success-with-a-warning"
category: bug
tags: [bug, windows, uv, update, self-update, tool-environment]
status: resolved
severity: P1
template_version: "bug-1.0.0"
related_docs:
  - uv-hardlink-fallback-warning.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# Windows: self-update destroys its own uv tool environment, then reports success-with-a-warning

**Status:** resolved

**Severity:** P1 — leaves `ai` unable to start at all until manually reinstalled.

**Platforms:** Windows only. The causal mechanism is Windows-specific mandatory
image locking; macOS and Linux unlink a running executable without complaint.

<!-- doc:region-end -->

## Symptom

Launching a session on a checkout with new commits triggered the auto-update, which
failed and appeared to be survivable:

```text
$ ai c 2
ai-cli-utils has new commits — running ai update --force...
Pulling latest from origin...
Updating 0.7.0 → 0.7.0.post<timestamp>
Resolved 19 packages in 378ms
error: failed to remove directory
       `C:\Users\<user>\AppData\Roaming\uv\tools\ai-cli-utils\Scripts`: Access is
       denied. (os error 5)
Warning: auto-update failed, continuing with current version
```

That last line was false. The session it was printed in continued fine, but the tool
environment had already been destroyed, so the *next* invocation died before doing
any work:

```text
File "C:\Users\<user>\.local\bin\ai.exe\__main__.py", line 4, in <module>
    from ai_cli.main import cli
ModuleNotFoundError: No module named 'ai_cli'
```

Measured state right after the failure:

- `uv tool list` → `Failed find package 'ai-cli-utils' in tool environment`. Other
  tools in the same uv root listed normally, so the damage was specific to the tool
  being upgraded.
- `<uv tools>/ai-cli-utils/Lib/site-packages/` — **gone entirely**.
- `<uv tools>/ai-cli-utils/Scripts/` — survived, but stripped to `python.exe`,
  `pythonw.exe`, and one unrelated dependency script. `ai.exe` was gone.
- The launcher shim `~/.local/bin/ai.exe` still existed, freshly rewritten, now
  pointing into a stripped environment.

## Causal mechanism

```text
`ai c` detects HEAD != update stamp
  → spawns `ai update --force` as a child process
  → child runs `uv tool install <project> --force --reinstall`
  → uv REPLACES the tool environment: delete, then rebuild
  → deleting Lib/site-packages succeeds
  → deleting Scripts/ fails: it holds python.exe, the mapped executable image of
    both the parent `ai c` process and this child
  → uv aborts non-atomically → environment left with no packages
  → child exits non-zero → parent prints "continuing with current version"
```

The load-bearing detail is **which file is locked**. It is not `ai.exe`; it is
`Scripts/python.exe`, the interpreter both live processes are executing. Windows
opens an executable image without `FILE_SHARE_DELETE`, so the file cannot be
unlinked while mapped, and a directory containing it cannot be removed. This is not
a permissions problem and elevation does not help — no privilege level can unlink a
mapped image opened that way.

Reproduced deterministically at the real boundary, independent of this codebase:
create a venv, run its `python.exe`, and `shutil.rmtree` its `Scripts` directory →
`PermissionError` with `errno=13`, `winerror=5`, naming `python.exe`. That
`winerror=5` is exactly the `os error 5` uv surfaced.

### Why it had to be non-atomic to matter

A failed upgrade would be harmless if uv rolled back. It does not: the teardown is
partial, so the environment is left in a state that is neither the old nor the new
version. That is what converts "update didn't apply" into "tool is bricked".

## Rejected hypotheses

| Hypothesis | Why rejected |
|---|---|
| Corporate/AV file-locking or missing admin rights | The same failure reproduces in a throwaway venv under the user's own temp dir, with no elevation involved. `Access is denied` here is image locking, not an ACL denial. |
| `ai.exe` is the locked file | The reproduction names `python.exe`. `ai.exe` is a trampoline; the interpreter is the mapped image. |
| uv bug needing a version bump | uv is behaving correctly — it cannot remove a directory Windows is protecting. The defect is asking it to replace the environment we are executing from. |
| Retrying, or deleting `Scripts` first, would work | Nothing can unlink the image while any process runs it, and the parent `ai` process is alive for the whole child run. |

## The RED test

`tests/test_windows_self_update.py`, written and confirmed failing before the
production edit. On the unfixed code the core test failed with the offending command
verbatim:

```text
update still recreates the live tool environment:
[uv.EXE, 'tool', 'install', <project>, '--force', '--reinstall']
```

Four tests, deliberately covering different levels:

1. Running from a uv tool environment → the update must not issue the
   environment-recreating command, and must target the live environment instead.
2. **Negative constraint** — not running from a tool environment → the ordinary
   `uv tool install` path must be unchanged. Without this, "never call
   `uv tool install`" would satisfy test 1 while breaking every normal install.
3. A failed auto-update with a broken environment must not print "continuing with
   current version", and must say the install is broken.
4. **Platform premise guard** (Windows-only) — a real venv, a real live interpreter,
   a real `rmtree`, asserting `winerror == 5`. This documents the OS behaviour the
   fix is built on; if Windows ever stopped locking mapped images it would go red and
   the rationale would need revisiting. It is not the regression test.

## The fix

`src/ai_cli/main.py`, two changes.

**1. Do not recreate the environment you are running from.** When the interpreter is
running from a uv tool environment, install into it rather than replacing it:

- Detection uses uv's own marker: `uv-receipt.toml` at `sys.prefix`. That identifies
  a tool environment exactly, with no `uv tool dir` round trip, and stays correct if
  uv relocates its directories.
- The in-environment install rewrites `Lib/site-packages` and never touches
  `Scripts`. Validated under a held lock: it exits 0 and the environment still
  imports afterward, while the destructive form fails.
- The ordinary path is untouched, guarded by test 2.

**2. Stop reporting a destroyed environment as usable.** On a failed auto-update,
probe the environment before characterising the failure, then either report it as
broken with the repair command, or state that the existing install is intact.

The probe must be a **subprocess** using the environment's own interpreter. An
in-process `import ai_cli` proves nothing, because the calling process already holds
the module resident — it would report a bricked environment as healthy.

## Verification

- Frozen tests: RED before the edit (for the reported reason), GREEN after, with the
  test file byte-identical across both runs.
- Hard gate: `ruff check`, `ruff format --check`, full `pytest` — all clean for this
  change.
- Pre-existing, unrelated: 5 failures + 158 errors across `test_session_adopt.py`,
  `test_session_audit.py`, `test_cc_migrate.py`, `test_bare_worktree.py`. Confirmed
  pre-existing by re-running them against the pre-fix revision and getting identical
  counts. They are Windows `MAX_PATH` (`WinError 206`) failures in fixtures that slug
  an already-long pytest temp path; tracked separately.

## Prevention lessons

1. **A failed destructive operation is not a no-op.** Any "we tried and it didn't
   work, carrying on" path must verify the thing it was mutating is still usable
   before reassuring the user. The false warning here was more harmful than the
   failure, because it moved the breakage to a later, unrelated-looking invocation.
2. **A process cannot safely replace the environment it is executing from.** Treat
   in-place self-replacement as a distinct case with its own code path, not as an
   ordinary install that happens to target the current environment.
3. **Health probes must not run in the process being probed.** Already-imported
   modules, warm caches, and open handles all mask a broken on-disk state.
4. **`Access is denied` on Windows is not necessarily a permissions problem.** Check
   for mandatory locking before concluding anything about ACLs or elevation — the
   admin-rights hypothesis here was plausible, cost nothing to test, and was wrong.

## Not addressed here

`AI-CLI-140` — this command converts an editable tool install into a copy. Same
command, separate defect. This fix deliberately preserves existing behaviour on that
axis rather than widening scope.
