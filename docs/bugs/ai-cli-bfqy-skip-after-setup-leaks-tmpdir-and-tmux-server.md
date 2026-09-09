---
title: "Capability probes ran after the setup they gate, and one fixture leaked its tmpdir and a live tmux server on the skip path"
category: bug
tags: [bug, tests, fixtures, resource-leak, skip, capability-probe]
status: fixed
source: "AI-CLI-bug-tests-skip-capability-probe-bfqy"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# [AI-CLI-bfqy] Capability probes ran after the setup they gate

**Status:** fixed

**Severity:** P2 — a resource leak on the skip path, plus wasted I/O on every run of every
affected host

**Created:** 2026-09-09

**Task:** `AI-CLI-bug-tests-skip-capability-probe-bfqy`

## Summary

Several tests and fixtures decide at run time whether the host can support them — is `zsh`
installed, is this filesystem case-insensitive, can an isolated `tmux` server start — and
they were making that decision *after* doing the expensive part.

Found while auditing the suite's 18 skips (`AI-CLI-i2ih` follow-up). An AST sweep found
**four** sites. Three were fixable; one is legitimate and is now allowlisted explicitly.

The one that matters is a real leak. `real_tmux_socket` called `mkdtemp`, started a probe
`tmux` server, and only *then* decided whether to skip — with the `try`/`finally` that
cleans both up beginning *below* the skip. On the skip path the temp directory was never
removed and the probe server was never killed. That path is by definition the one taken on
hosts where tmux misbehaves, which is exactly where it does the most damage.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

Drive the fixture's generator with every `tmux` call forced to fail — the condition its
second skip exists for — and watch what is left behind:

```python
generator = isolated_tmux_socket()  # against the unfixed code
next(generator)  # raises Skipped
# the mkdtemp'd directory is still on disk, and `tmux -S <socket>` still has a server
```

The sweep that found all four sites, run over `tests/`:

```
LEAKS   tests/test_session_reload_mtime.py:99          skip() after ['write_text']
LEAKS   tests/test_stale_session_reaper.py:131         skip() after ['mkdtemp']
LEAKS   tests/test_stale_session_reaper.py:1187        skip() after ['mkdir', 'write_text']
LEAKS   tests/test_worktree_container_collision.py:299 skip() after ['create_worktree']
```

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause Analysis

One shape, written four times independently, which means it is attractive rather than
accidental: *acquire, then validate.* In a test that is normally harmless because the
runner cleans `tmp_path` up; the leak appears the moment the acquisition happens outside
`tmp_path` (here, `mkdtemp(dir="/tmp")`) or starts a process.

Per site:

| Site | What it did first | Cleaned up on skip? |
| --- | --- | --- |
| `test_stale_session_reaper.py:131` `real_tmux_socket` | `mkdtemp` in `/tmp` + started a probe tmux server | **No** — the `try`/`finally` began below the skip |
| `test_worktree_container_collision.py:299` | a full `create_worktree` checkout | Yes, via `tmp_path`; pure waste |
| `test_stale_session_reaper.py:1187` `_start_generated_supervisor` | a bin dir and several generated executables, ~60 lines before a `shutil.which("script")` check | Yes, via `tmp_path`; pure waste |
| `test_session_reload_mtime.py:99` | one `write_text` | Yes — **and this one is correct**, see below |

**The site that must not be "fixed".** `test_the_old_probe_chain_is_unstable_on_this_platform`
writes a file so that it can `stat` it: the write *is* the probe's input, so it cannot be
hoisted above the probe. Silently exempting it would leave the next reader to rediscover
why, so the guard allowlists it by name with the reason attached.

### Why `tmp_path` retention was not the answer

`pyproject.toml` sets `tmp_path_retention_policy = "failed"`, so a skipped test's directory
*is* discarded — which is why three of the four sites cost only I/O. That is also why the
leak hid: the policy makes the common case self-correcting, so nobody looked at the one
acquisition that happens outside `tmp_path`.

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="options" kind="replaceable" -->

## The Fix

There are exactly two correct shapes, and the fix uses whichever fits:

1. **Probe before you build**, when the probe needs nothing.
   - `tests/conftest.py` gains `filesystem_is_case_insensitive(path)` and a
     `case_insensitive_filesystem` fixture, so the case-alias test skips at *setup* and
     never enters a body that would check out a worktree. The probe asks the filesystem —
     make a directory, stat the other spelling — rather than guessing from `sys.platform`,
     because a case-insensitive volume can be mounted on Linux and a case-sensitive one on
     macOS. It removes its own probe directory.
   - `_start_generated_supervisor` resolves `shutil.which("script")` at the top. A `PATH`
     lookup builds nothing, so hoisting it is free.
2. **Build first, but make the cleanup unconditional**, when the probe genuinely needs the
   setup.
   - `real_tmux_socket`'s body became a module-level generator, `isolated_tmux_socket`, with
     the `try` opening *above* the probe so the `finally` runs as the `Skipped` exception
     propagates. It is a plain generator rather than a bare fixture so a test can drive its
     cleanup contract directly. The `which("tmux")` skip stays above the `mkdtemp`, since
     that one has nothing to clean up.

## Prevention

`tests/test_skip_hygiene.py` carries an AST meta-guard: no function may call `pytest.skip`
after an expensive call unless the skip sits inside a `try` with a `finally`, or the site is
allowlisted with a reason. It accepts *both* correct shapes deliberately — a rule that
forbade the try/finally form would forbid the correct fix to its own findings, which is how
a guard ends up worked around instead of satisfied.

The guard is itself guarded, because an AST walk that matches nothing passes forever:

- a **planted violation** must be found (positive control),
- a **correctly ordered** probe must not be flagged (negative control),
- a **try/finally** skip must not be flagged, and a `try/except` with no `finally` **must**
  be — a bare `except` proves nothing about cleanup,
- a **stale allowlist entry** that matches no live site fails the suite, so an exemption
  cannot outlive the code it covers and quietly shelter the next offender in that file.

Structure is all an AST walk can assert, so the behavioural tests carry the semantics: the
leak case asserts against the real filesystem that the directory is gone, with a positive
control that one was really created, plus an anti-vacuity test that the success path still
cleans up — without which "delete it at the top of the fixture" would pass.

## Verification

- `tests/test_skip_hygiene.py` — 12 tests. RED before the fix for the right reasons: the
  meta-guard reported all three real offenders by name, and the leak test showed the
  directory surviving.
- Full gate: `ruff check`, `ruff format --check`, **2759 passed, 20 skipped, 0 failed**.
- Skip count moved 18 → 20 as expected, and the two added skips are this file's own
  case-insensitive-filesystem cases declining to run on a case-sensitive host — which is the
  fixture working.

<!-- /doc:region name="options" -->

<!-- doc:region name="lessons" kind="replaceable" -->

## Lessons Learned

1. **Acquire-then-validate is the bug; validate-then-acquire or acquire-under-`finally` are
   the fixes.** Four independent authors wrote the first one, so code review will not catch
   the fifth — a mechanical guard will.
2. **A cleanup contract that starts below an early return is not a cleanup contract.** The
   `try`/`finally` looked present in review; what mattered was the line it began on.
3. **A framework convenience can hide a leak in the cases it does not cover.**
   `tmp_path` retention made three of four sites harmless, and that harmlessness is why the
   fourth went unexamined for so long.
4. **An allowlist entry needs a reason and an expiry check.** Both were added; the stale-entry
   test is what stops the exemption from becoming cover for unrelated code later.

<!-- /doc:region name="lessons" -->
