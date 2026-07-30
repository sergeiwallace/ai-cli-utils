---
title: "[BUG-008] Every `ai update` and auto-update prints hardlink-fallback warning when cache and tool dirs are on different filesystems"
category: bugs
tags: [uv, update, filesystem, hardlink, warning]
status: fix-deployed
severity: P3
related_docs:
  - CHANGELOG.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-008] Every `ai update` and auto-update prints hardlink-fallback warning when cache and tool dirs are on different filesystems

**Status:** fix-deployed

**Severity:** P3 — cosmetic only (the install always succeeds), but the warning
fires on every update, training users to ignore update output.

**Created:** 2026-07-30

**Task:** AI-CLI-148

## Symptoms

Every `ai update` — and the auto-update that runs at `ai c N` launch — prints
this to stderr:

```
warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance.
         If the cache and target directories are on different filesystems, hardlinking may not be supported.
         If this is intentional, set `export UV_LINK_MODE=copy` or use `--link-mode=copy` to suppress this warning.
```

The install succeeds and is correct. The warning is cosmetic, but it fires on
every single update (and every auto-update at session launch), which trains the
user to ignore update output entirely.

## Root cause

uv's cache directory and its tool-install target directory reside on different
filesystems, so hardlinking is physically impossible. On an affected machine the
two paths report different `st_dev` values, for example:

- uv cache dir → a network filesystem (NFS), because `UV_CACHE_DIR` is exported
  from the shell profile to a shared/network location.
- tool root (`~/.local/share/uv/tools`) → a local disk filesystem.

When uv attempts to hardlink a file from cache to the tool directory and the
link fails (errno EXDEV, cross-device link), it falls back to copying and emits
this warning. The fallback is correct and performs identically on subsequent
runs, so the warning is purely advisory — but it fires unconditionally on every
update because the condition is structural, not transient.

This is not machine-specific in principle: any split-mount layout hits it
(containers, NFS/EFS homes, a separate /home volume, a cache on fast SSD with
home on spinning rust).

## Fix

Detect the `st_dev` mismatch between uv's resolved cache directory and the
install target directory (tool dir for `uv tool install`, venv path for
`uv pip install`), and pass `--link-mode=copy` only when they differ.

Three call sites updated:

1. `src/ai_cli/main.py:_do_update` — the main `ai update` path: `uv tool install ... --force [--reinstall] [--link-mode=copy]`
2. `src/ai_cli/main.py:trigger_background_update` — auto-update at session launch: `uv tool upgrade ai-cli-utils [--link-mode=copy]`
3. `src/ai_cli/main.py:cmd_upgrade` — explicit `ai upgrade`: `os.execvp("uv", ["uv", "tool", "upgrade", "ai-cli-utils", ...])`
4. `src/ai_cli/main.py:_do_update` extra_venvs loop — `uv pip install` into configured venvs: `uv pip install ... [--force-reinstall] [--link-mode=copy]`

Detection is automatic and portable:

- Resolve uv's cache directory via `uv cache dir` (not `UV_CACHE_DIR` directly)
  to match uv's own resolution (env var > platform default).
- Resolve the tool install directory via `uv tool dir`, with fallback to the
  platform-appropriate default (`~/.local/share/uv/tools` on Unix) if that
  prints nothing.
- For venv installs, compare cache against the specific venv path.
- If the target directory does not exist yet (first install), walk up to the
  nearest existing ancestor before stat'ing — `st_dev` is an inode property, so
  any ancestor on the same filesystem carries the same value.
- On any failure (uv not found, command error, path stat error), return False
  to preserve the current "no explicit --link-mode" behavior — never let this
  detection break an update.

The decision is portable: `os.stat().st_dev` comparison works on Linux, macOS,
and Windows (Windows st_dev semantics differ — it's a volume serial number —
but comparing two values for equality is still valid).

Critical constraint: do **not** hardcode `--link-mode=copy` unconditionally.
That would discard the hardlink fast path on normal single-filesystem machines,
which is a real performance regression for most users.

## Verification

- Baseline: 2002 passed / 0 skipped / 0 failed. After: 2007 passed / 0 skipped / 0 failed (5 new tests).
- New tests cover: (a) same filesystem → no `--link-mode` flag, (b) different
  filesystems → `--link-mode=copy` present, (c) cache dir unresolvable → no
  flag (preserve default behavior), (d) target dir does not exist yet (walks up
  to ancestor).
- Ruff check + format --check: clean.

## Lesson

When a tool's warning message includes the exact flag to suppress it, and the
condition is structural rather than transient, detect the condition and pass the
flag automatically rather than asking the user to export an env var or manually
add the flag to every call. The detection cost (two cheap subprocess calls for
`uv cache dir` and `uv tool dir`, two stat calls) is negligible relative to an
update, and the payoff is eliminating a standing false alarm.

<!-- /doc:region name="summary" -->
