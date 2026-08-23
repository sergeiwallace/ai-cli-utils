---
title: "Windows test suite unusable — pytest 0o700 protected DACL blocks git, and MAX_PATH overflows the slugified project dir"
category: bug
tags: [bug, windows, pytest, acl, dacl, max-path, test-infrastructure, portability, direnv]
status: resolved
source: "AI-CLI-windows-test-suite-failures-nvqt"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# [AI-CLI-windows-test-suite-failures-nvqt] Windows test suite unusable — pytest `0o700` protected DACL blocks git, and MAX_PATH overflows the slugified project dir

**Status:** resolved — both classes fixed with ordinary user privileges, verified on a Windows host

**Severity:** P2 (blocks using the suite as a gate on every Windows machine; no production impact)

**Created:** 2026-08-20

**Task:** AI-CLI-windows-test-suite-failures-nvqt (hash AI-CLI-rfp)

**Fixed by:** `tests/conftest.py` (both classes) and `src/ai_cli/direnv_setup.py` (the PATH-staleness bug found alongside) — commit `51e56a62`

**Related issues:**
- `AI-CLI-ai-cli-utils-abvz` — the umbrella OS-agnostic task this came out of
- `AI-CLI-session-reclamation-linux-only-zusn` — separate portability defects (F1/F2) found by the same audit, NOT fixed here
- `AI-CLI-ai-c-direnv-jsqn` — the direnv warning bug whose investigation surfaced all of this (PR #26)

## Summary

Running `pytest` on a Windows host reported roughly **105 failures and 158 errors**. Neither number reflected a defect in `src/` — identical counts reproduced on `main` with no local changes. Both were test-infrastructure problems caused by how pytest creates temporary directories on Windows, and both are now fixed **without admin rights, registry edits, or repairing the machine's domain trust**.

An initial investigation wrongly concluded the fixes were IT-level. That conclusion was based on a remedy tested in the wrong order (see "The false negative" below) and is corrected here.

Two independent root causes:

1. **Protected DACL (~105 failures).** pytest creates every temp directory with `mode=0o700`. CPython on Windows implements that as a *protected* DACL — inheritance disabled, ACEs for `SYSTEM`, `Administrators` and `OWNER RIGHTS` only, and **no ACE for the current user**. Git for Windows runs its own MSYS access check against that DACL and refuses to create anything underneath.
2. **MAX_PATH (~158 errors).** `LongPathsEnabled` is `0` and needs admin to change. pytest nests `<temp>/pytest-of-<user>/pytest-N/<test-name>/`, and several tests then build a Claude Code project directory whose *name* is an entire absolute path with every non-alphanumeric byte replaced by `-`. Paths crossed 260 characters.

## Class 1 — protected DACL blocks git

### Symptom

```
fatal: could not create leading directories of '<path>': Permission denied
```

`git init` fails the same way (rc=128). Affected every test whose fixture builds a real git repository in `tmp_path`.

### Measured evidence

Isolated entirely outside pytest, creating the parent from Python and then cloning into a nested subdirectory:

| Parent created by Python | `git init` | `git clone` |
|---|---|---|
| `mkdir(mode=0o700)` | rc=128 | rc=128 |
| `mkdir(mode=0o777)` | rc=0 | rc=0 |
| `mkdir(mode=0o700)` then `icacls /reset /T` | rc=0 | rc=0 |
| `mkdir(mode=0o700)` then `icacls /inheritance:e /T` | rc=0 | rc=0 |

ACL dump showing the mechanism:

```
# mkdir(mode=0o700) — inheritance broken, no user ACE
NT AUTHORITY\SYSTEM:(OI)(CI)(F)
BUILTIN\Administrators:(OI)(CI)(F)
OWNER RIGHTS:(OI)(CI)(F)

# mkdir(mode=0o777) — all inherited, INCLUDING the user's own ACE
NT AUTHORITY\SYSTEM:(I)(OI)(CI)(F)
BUILTIN\Administrators:(I)(OI)(CI)(F)
<DOMAIN>\<user>:(I)(OI)(CI)(F)
```

### Compounding factor

`icacls` on this host prints `The trust relationship between this workstation and the primary domain failed`. With the domain trust broken the user's SID no longer resolves cleanly, so `OWNER RIGHTS` does not rescue the access check. This is why the defect is visible here and may not reproduce on a healthy domain-joined machine — but the fix is unconditional and costs nothing where the problem is absent.

### The false negative that produced the wrong first conclusion

The remedy was first tested **after** `git init` had already failed inside the directory. The subsequent clone then failed with a *different* and misleading error (`fatal: '<path>/remote' does not exist`) because the source repository had never been created. That was read as "the remedy does not work," and the whole class was filed as IT-only.

Re-tested with the repair applied **before** any repository is created, both remedies work. **Ordering is part of the fix, not an incidental detail.**

### Fix

A lazy repair, applied on the first `git` subprocess a test issues, inside the subprocess guard in `tests/conftest.py` that already wraps every call.

Laziness is deliberate. Repairing unconditionally per test also worked, but each repair spawns an `icacls` process and measured roughly **double** the runtime of a fast test file (26s to 56s), while only a small minority of tests shell out to git.

### Rejected approaches, with reasons

Recorded in code comments so they are not re-litigated:

- **`tmp_path.chmod(0o777)` from Python.** On Windows `chmod` only toggles `FILE_ATTRIBUTE_READONLY`; it cannot rewrite a DACL. Written, measured ineffective, reverted rather than shipped.
- **`icacls /grant *<user-SID>:(OI)(CI)F` on a leaf directory alone.** Grant returned rc=0; the clone still failed rc=128.
- **Repairing only the session roots.** Insufficient — pytest applies `mode=0o700` to *each per-test directory*, which does not exist yet when the roots are repaired.
- **Patching pytest to stop requesting `0o700`.** The mode is hardcoded at **five** call sites in `_pytest.tmpdir`, and `getbasetemp` additionally *validates* that the root is not group/world accessible (`if (rootdir_stat.st_mode & 0o077) != 0`), so forcing it wide fights pytest's own security check. Attempted and abandoned; it also broke on `mode` being passed positionally.

## Class 2 — MAX_PATH overflow

### Symptom

```
FileNotFoundError: [WinError 3] The system cannot find the path specified: '<~250-270 char path>'
```

The failing calls are Python's own, e.g. `tests/test_session_audit.py` and `tests/test_session_adopt.py` doing `project_dir.mkdir(mode=0o700, parents=True, exist_ok=True)` on a path derived from `cc_project_dir(repo, home)`.

### Why the slug cannot simply be shortened

`_cc_project_dir` in `src/ai_cli/main.py` must keep matching Claude Code's own slugify (every non-alphanumeric byte becomes `-`) or session resume looks in the wrong directory. The length is a requirement of the format, not an accident.

### Measured evidence

| Path | Result |
|---|---|
| plain `mkdir`, 270 chars | FAIL `winerror=3` |
| plain `mkdir`, 91 chars | OK |
| `\\?\` extended-length prefix, 271 chars | OK |

### Fix

Relocate the temp root through pytest's own `PYTEST_DEBUG_TEMPROOT` hook to a short directory on the same drive as the default temp dir. The default root here is 40 characters; the replacement is about 7.

The leverage is better than it first appears: because the slugified directory *name* embeds the temp path, shortening the root shortens the path **twice over** — once in the real path and again inside the slug.

The `\\?\` prefix was verified to work and remains the fallback if a future path still overflows, but it would have to be applied at each construction site, whereas the root relocation is a single change that covers every affected test.

## Bug found alongside: stale PATH made a good direnv install look failed

Not part of the original report, found while verifying the direnv bootstrap shipped in PR #50.

A package manager writes its install directory to the **registry**, not into an already-running process's environment block. So after `install_direnv()` succeeded, `shutil.which("direnv")` in the same process still could not see the new binary, and a perfectly good install was reported as a failure.

This is also the real explanation for the symptom that started the whole investigation: `direnv: not a command` was reported while direnv **was** in fact installed (chocolatey and winget, v2.37.1) — the shell had simply started before the install and kept its old PATH.

**Fix:** `refresh_windows_path()` re-reads the persisted machine and user PATH from the registry and merges in anything new, and `install_direnv` calls it before verifying. Existing entries keep their position so a deliberately prepended directory retains precedence. A parent shell cannot be fixed from a child process, so the remediation text now prints the in-place refresh one-liner per shell (PowerShell and Git Bash) instead of only saying "restart".

## Verification

- Both previously-failing git-permission tests pass: `tests/test_cli.py::TestDeploy::test_deploy_when_autostash_pop_conflicts_then_aborts_instead_of_installing` and `tests/test_cli.py::TestCliWorktreeGitPull::test_when_worktree_conflict_predates_launch_then_resolution_is_not_discarded`.
- `tests/test_direnv_setup.py` — 50 passing, including four new tests covering the PATH refresh and one regression test proving an install that only becomes visible after the refresh is reported as installed.
- `ruff check` and `ruff format --check` clean across `src/` and `tests/`.
- Full-suite before/after counts: baseline ~105 failed / ~158 errors.

## Platform safety

Every mechanism is gated on `sys.platform == "win32"`. `tests/conftest.py::_make_tmp_path_deletable` remains deliberately unconditional for its original teardown purpose — POSIX `rmtree` only needs write on the parent, so it is a cheap no-op there. The POSIX `0o700` is a genuine multi-user protection on a shared `/tmp` and is left untouched.

<!-- /doc:region -->

<!-- doc:region name="decisions" kind="replaceable" -->

## Decisions Requiring Team Input

Four decisions remain open. Each is a trade-off already made in the shipped code (or deliberately deferred) that is worth ratifying or overriding.

### Decision Summary

| ID | Decision | Status | Chosen |
|---|---|---|---|
| D-1 | Where the shortened Windows temp root lives | Open | (a) provisionally |
| D-2 | Lazy vs unconditional DACL repair | Open | (a) provisionally |
| D-3 | Whether to set `core.longpaths` as machine config | Open | none |
| D-4 | Non-ASCII in console output repo-wide | Open | none |

### D-1 — Where the shortened Windows temp root lives

**Context:** Class 2's fix needs a short temp root. The default is ~40 characters; every character saved is worth two because the slug embeds the path. The shipped code derives the drive from the default temp dir and appends a short name, giving something like `C:\aipt`. Creating a directory at the drive root is unusual, though it was verified to need no admin.

##### (a) Short directory at the drive root

**Pros:**
- Maximum path headroom, which is the entire point of the fix.
- Verified creatable without admin on this host.
- Drive derived at runtime, so it does not assume `C:`.

**Cons:**
- Clutters the drive root, which some operators consider off-limits.
- An opaque name (`aipt`) is not self-explanatory to someone finding it later.
- May be denied on a locked-down host, though the code falls back to the default root.

##### (b) Short directory under the user profile

**Pros:**
- Conventional location; nothing appears at the drive root.
- Inherits the user's own full-control ACE, which is the known-good ACL condition.

**Cons:**
- Saves almost nothing: `C:\Users\<user>\AppData\Local` is already ~37 characters, so the MAX_PATH problem largely survives.
- Would leave Class 2 needing the per-site `\\?\` prefix work after all.

##### (c) Keep the default root and apply `\\?\` at every construction site

**Pros:**
- No new directory anywhere.
- `\\?\` is the officially correct way to exceed MAX_PATH and was measured working at 271 characters.

**Cons:**
- Must be applied at each site that builds a long path, and any new site silently reintroduces the bug.
- Spreads a Windows detail across otherwise platform-neutral test helpers.

**Recommendation:** (a), with the fallback already implemented. It is the only option that actually solves Class 2 in one place, and the failure mode is benign — if the directory cannot be created the code silently keeps the default root and only Class 2 returns. Mitigate the naming con by choosing a self-describing name if the root location is accepted.

### D-2 — Lazy vs unconditional DACL repair

**Context:** The Class 1 repair can run for every test or only for tests that actually invoke git. Unconditional is simpler to reason about; lazy is measurably faster. Shipped code is lazy, triggered from the existing subprocess guard.

##### (a) Lazy — repair on the first `git` subprocess in a test

**Pros:**
- Measured roughly half the runtime of unconditional on a fast test file (26s vs 56s baseline).
- Only tests that need the repair pay for it, and they are a small minority.
- Reuses the subprocess wrapper that already inspects every call, so there is no new interception layer.

**Cons:**
- Couples a filesystem repair to a subprocess guard, which is a surprising place to find it.
- A test that needs the repair without ever spawning `git` (a hypothetical library-level git binding) would not get it.
- Slightly harder to reason about than "always".

##### (b) Unconditional — repair every `tmp_path` at setup

**Pros:**
- Trivial to reason about; no trigger condition to get wrong.
- Cannot miss a case regardless of how a test reaches the filesystem.

**Cons:**
- Spawns an `icacls` process per test; measured roughly double runtime on a fast file.
- Across ~2300 tests that cost is paid overwhelmingly by tests that never touch git.

**Recommendation:** (a). The runtime difference is large and paid on every run by every developer, while the missed-case con is hypothetical — nothing in this repo drives git through anything but a subprocess. Mitigate by keeping the trigger documented at the call site, so a future git-via-library test is an obvious extension point rather than a silent gap.

### D-3 — Whether to set `core.longpaths` as machine configuration

**Context:** `git config --global core.longpaths true` is writable without admin and currently unset. It would help git-side long paths. The Class 2 failures observed were all Python-side `mkdir` calls, so this changes nothing measured — but it is cheap insurance against the git-side variant.

##### (a) Leave it unset

**Pros:**
- No machine state changed; the in-repo fix stands alone and works on any host.
- Nothing to remember or re-apply on a new machine.

**Cons:**
- A future git-side long-path failure would need diagnosing from scratch.

##### (b) Set it globally on Windows machines

**Pros:**
- Removes a whole latent failure class for a one-line, no-admin change.
- Matches what most Windows git users end up doing anyway.

**Cons:**
- Per-machine configuration, which the fix deliberately avoided needing.
- Invisible state: a suite that only passes because of it is not reproducible on a fresh machine.

**Recommendation:** No recommendation without input, because it turns on whether per-machine setup is acceptable at all. If the standing preference is "no per-machine configuration," (a) is consistent with the rest of this fix. If a documented Windows bootstrap step is acceptable, (b) is nearly free.

### D-4 — Non-ASCII in console output repo-wide

**Context:** Console output elsewhere in the codebase contains em dashes and box-drawing characters. On a cp1252 Windows console these render as replacement characters. `src/ai_cli/quota.py` reconfigures `sys.stdout` to UTF-8 on win32, but only stdout and only in that module — stderr and every other module are unprotected. The ruff config deliberately ignores `RUF001`/`RUF002`/`RUF003` with the note that box-drawing symbols in CLI output are intentional, so this is a standing decision, not an oversight. `direnv_setup.py` is ASCII-only in all printed output as a reference point.

##### (a) Make all printed output ASCII-only

**Pros:**
- Correct on every console with no runtime setup.
- Trivially testable with a codepoint assertion.

**Cons:**
- Loses intentional box-drawing that improves readable CLI output on capable terminals.
- A broad sweep across many modules, with churn in user-visible strings.

##### (b) Reconfigure stdout and stderr to UTF-8 on Windows at entry

**Pros:**
- Keeps the richer output everywhere it works.
- One change at the entry point instead of a repo-wide sweep.

**Cons:**
- Does not help output captured by a tool that assumes the console codepage.
- `reconfigure` is unavailable on a non-TextIO stream, so it needs a guard.

##### (c) Leave as-is

**Pros:**
- Zero work and zero risk.

**Cons:**
- Windows users keep seeing replacement characters in exactly the messages meant to help them.

**Recommendation:** (b), scoped to the CLI entry point. It preserves the deliberate choice the ruff config records while fixing the platform it breaks on, and it is far smaller than a repo-wide ASCII sweep. Keep ASCII-only discipline for remediation and error text specifically, where the message must survive any console.

<!-- /doc:region -->
