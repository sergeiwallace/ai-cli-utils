---
title: "`ai c` dies at `tmux new-session` when tmux's shared library vanishes — presence was treated as runnability"
category: bug
tags: [bug, tmux, native-dependencies, dynamic-loader, self-healing, launcher]
status: fixed
source: "AI-CLI-i2ih"
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# [AI-CLI-i2ih] `ai c` dies at `tmux new-session` when tmux's shared library vanishes

**Status:** fixed

**Severity:** P2 — every session launch on the affected host fails, with a worktree already created

**Created:** 2026-09-09

**Tasks:** AI-CLI-i2ih (auto-provision tmux's runtime dependencies), AI-CLI-d89q (make the health check assert execution, not resolution)

**Related:**
- `docs/bugs/CORE-6nr-tmux-bare-call-windows-unsupported.md` — the other end of the same "tmux is optional" contract

## Summary

On a host whose container filesystem is rebuilt on restart while the per-user prefix
persists, a `tmux` built into that prefix loses the shared library it was linked against.
The binary stays on `PATH` and stops running.

`ai c` then reported the failure and continued anyway:

```
ai-cli: tmux version unavailable (binary does not run) at /home/user/.local/bin/tmux
ai-cli: launching inside tmux (tmux is the default session mode).
[launch] Worktree: reusing .../.worktrees/session-1
[launch] Worktree: ready
Error: failed to create tmux session 'c-session-1'
  (with --): tmux: error while loading shared libraries: libevent_core-2.1.so.7: cannot open shared object file: No such file or directory
  (without --): tmux: error while loading shared libraries: libevent_core-2.1.so.7: cannot open shared object file: No such file or directory
```

Three places conflated "on PATH" with "usable", and one place had the right answer and
did not act on it. The library itself was never gone — a copy sat in a *persistent*
directory one level below the same prefix, and tmux ran the moment the loader was pointed
at it. So the fix is discovery and fallback, not installation.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

## Reproduction

**Environment:** Linux, `tmux` installed into a persistent per-user prefix
(`~/.local/bin/tmux`), linked against a `libevent` provided by a package in the
non-persistent system image. Restarting the machine rebuilds the system image.

**1. The binary is present and unrunnable.**

```
$ which tmux
/home/user/.local/bin/tmux
$ tmux -V
tmux: error while loading shared libraries: libevent_core-2.1.so.7: cannot open shared object file: No such file or directory
$ echo $?
127
$ ldd /home/user/.local/bin/tmux | grep libevent
 libevent_core-2.1.so.7 => not found
```

**2. The library is still on the box, in a persistent directory.**

```
$ ls ~/.local/lib/*/usr/lib/libevent_core*
/home/user/.local/lib/tmux-appimage/usr/lib/libevent_core-2.1.so.7
/home/user/.local/lib/tmux-appimage/usr/lib/libevent_core-2.1.so.7.0.1
$ LD_LIBRARY_PATH=/home/user/.local/lib/tmux-appimage/usr/lib tmux -V
tmux 3.7c
```

**3. The package agreed tmux was fine.** Against the unfixed code on that host:

```
tmux_present() = True
tmux_runs()    = False
ensure_tmux()  = InstallResult(installed=True, tool='already-present', detail='')
probe()        = TmuxReport(present=True, path='/home/user/.local/bin/tmux',
                            client_version=None, server_version=None, versions_probed=True)
probe().runs   = False
```

`ensure_tmux()` reporting success while `probe().runs` is `False` is the defect in one
line: the launcher asked the question that returns `True` and the reporter asked the
question that returns `False`, and only the reporter was right.

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

## Root Cause Analysis

**Causal chain.**

1. The tmux binary lives in a persistent prefix; its `libevent_core-2.1.so.7` came from
   the machine's non-persistent system image.
2. A restart rebuilt that image. The binary survived, the library did not, so `tmux -V`
   exits 127 in the dynamic loader — before any tmux code runs.
3. `tmux_setup.ensure_tmux()` short-circuited on `tmux_present()`, returning
   `InstallResult(True, tool="already-present")`. `main._do_session_launch`'s preflight
   was guarded by `not tmux_present()`, so it never even called `ensure_tmux`.
4. `tmux_setup.probe()` correctly produced `runs=False`, and `report_lines()` printed
   `version unavailable (binary does not run)` — but **nothing consulted
   `report.runs`**. The launch printed `launching inside tmux`, created and
   synchronized a worktree, and then died at `tmux new-session` with the loader's error
   and `sys.exit(1)`.
5. `install_tmux()` verified its work with `verify=tmux_present`, so even the install
   route could not have distinguished a working tmux from a broken one.

**The interesting part is step 4, not step 2.** The environment breaking is an
environment problem. What made it a *launcher* problem is that the launcher already had
the fact in hand, printed it, and then acted against it — and the ordering meant the
operator paid for a worktree checkout before finding out.

### Hypotheses rejected

| Hypothesis | Why rejected |
| --- | --- |
| The library is genuinely missing and must be installed | Found at `~/.local/lib/tmux-appimage/usr/lib/libevent_core-2.1.so.7`; `LD_LIBRARY_PATH` alone made tmux run |
| The tmux binary is corrupt or built for the wrong libc | `tmux -V` prints `tmux 3.7c` and exits 0 once the loader path is set |
| A soname mismatch, as in the earlier `libutempter.so.0` report | Not this defect: `ldd` shows `libevent_core-2.1.so.7 => not found` and a file of *exactly* that name exists. No ABI-guessing symlink is needed, so none was added |
| `ai`'s Python dependencies are stale or the tool install is broken | The launcher's own install check reported `editable checkout 0.8.0…; current`, and the failure is in a C binary pip/uv cannot supply at all |

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="options" kind="replaceable" -->

## Fix Options and the recorded decision

AI-CLI-i2ih's AC-7 required an explicit choice between four options.

> **Decision:** `(1)+(4)` — option 1 (vendor the libraries + a launch wrapper), adopted in
> **discovery** form rather than by shipping copies, plus option 4 (detect and fail loudly) as an
> unconditional floor. Option 3 is retained for an **absent** tmux only; option 2 is rejected.

<!-- decision-record: chosen-option=(1)+(4); ai-family=claude; ai-model=us.anthropic.claude-opus-5[1m]; ai-effort=high; ai-profile=session-driver -->
<!-- decision-lineage: decision-id=AI-CLI-i2ih/D-1; decision-topic=how-to-provision-tmux-native-runtime-dependencies; governs=src/ai_cli/native_deps.py:repair_loader_path; normalized-proposition=a-missing-shared-library-is-repaired-by-discovering-it-on-the-host-not-by-vendoring-or-installing; applicability=package:ai-cli-utils,platform:posix; outcome-id=tmux-launch-self-heals-after-a-host-rebuild; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

**Decided by Claude Opus 5 (`us.anthropic.claude-opus-5[1m]`), 2026-09-09**, under the
Authority test: the choice is contained, reversible, and touches no interface outside this
package.

| Option | Verdict |
| --- | --- |
| **1. Vendor the libraries + a launch wrapper** | **Adopted, in discovery form.** Locate the library the loader named, in directories already on the machine, and prepend them to the loader's search path. No copies are shipped or fetched, so this package never becomes the owner of a private copy of system libraries or their staleness — which was option 1's stated cost, and the only reason it was not the obvious answer |
| **2. Fetch a static or self-contained tmux** | Rejected. Sourcing, trusting and verifying a prebuilt binary per platform from inside a Python tool install is a far larger blast radius than reading a directory that is already there |
| **3. Package manager when available** | **Kept for an ABSENT tmux only**, unchanged in mechanism but now verified by execution rather than by presence. Deliberately *not* used for a present-but-broken tmux — see below |
| **4. Detect and fail loudly with an actionable message** | **Adopted as the unconditional floor**, as recommended. A broken tmux must never again be silent, and the fallback is bare mode rather than a failed launch |

### Two refinements the full test suite forced, both kept

Neither was in the first draft of the fix, and both are improvements on it.

**A present-but-broken tmux is never sent to a package manager.** The first version
escalated repair → install for every unusable tmux. Running the full suite showed 32 launch
tests entering the install path mid-launch, which made the cost visible: a manager cannot
be verified to have fixed *the tmux that will actually run*, because the broken binary
keeps its place on `PATH`. So the launch would pay the manager's full timeout — up to five
minutes, on every launch, indefinitely — to change nothing, while also installing a second
tmux over one the operator placed by hand. The install route now covers the absent case
only, which is the one a manager can actually answer.

**Degrade only on positive evidence, never on an unparsed version string.**
`InstallResult` gained `unusable`, distinct from `not installed`. `unusable` means
established: absent from `PATH`, or present and naming a library it cannot load. A tmux
that runs but answers `-V` in an unexpected shape produces `installed=False,
unusable=False` — "could not confirm" — and the launch stays under tmux. Degrading there
would trade detach/reattach away over a parsing quirk, which is a worse bug than the one
this preflight exists to catch, and it is why `probe()`'s deliberate collapsing of every
failure mode to `client_version=None` must not by itself decide the session mode.

Two smaller corrections in the same pass: the preflight is skipped for a **remote**
dispatch (the tmux that hosts the session is on the remote host, so the local binary's
health is a fact about the wrong machine — the same reason the report was already skipped
there), and a successful loader repair no longer prints `tmux was auto-installed via
loader-path`, which claimed a machine mutation that never happened.

**Deliberately out of scope: the soname-mismatch variant.** The earlier report of
`libutempter.so.0` wanted while `libutempter.so.1.2.1` was present would need a
compatibility symlink, which is an ABI bet. That variant does not reproduce on this host
(`ldd` shows tmux 3.7c does not link `libutempter` at all), and building machinery for an
unreproduced failure is how speculative fixes get shipped. If it recurs, it is its own
issue with its own evidence.

## The Fix

**`native_deps.py` — a generic, platform-aware loader repair.**

- `loader_path_var()` — `LD_LIBRARY_PATH`, `DYLD_LIBRARY_PATH` on macOS, and `None` on
  Windows, which resolves DLLs through `PATH` and has no equivalent variable.
- `missing_shared_libraries(stderr)` — parses the loader's own words (glibc, musl, and
  macOS dyld wordings), not `ldd`/`otool`: the process that just failed has already named
  what it wanted, and `ldd` exists on neither musl nor macOS.
- `candidate_library_dirs(exe)` — `<prefix>/lib`, `<prefix>/lib64`, and one nesting level
  below for self-contained payload layouts (`<prefix>/lib/<payload>/usr/lib`, which is
  where the surviving copy actually was). Symlinks are resolved so a launcher symlink
  into an unpacked tree finds that tree's libraries. `AI_CLI_LIBRARY_PATH` overrides.
  Only existing directories are returned — a nonexistent entry in the search variable is
  silently ignored by the loader, which would make a doomed repair look real.
- `repair_loader_path(argv)` — runs the command, reads the complaint, prepends what it
  finds, and **re-runs to prove it worked**. A repair that cannot be demonstrated is
  rolled back, because a speculative search path left in the environment changes how
  every later child process resolves its libraries.

**`tmux_setup.py`** — `ensure_tmux()` routes by *why* tmux is unusable: absent → one
unattended install (unchanged); present but not running → repair the loader path, and
nothing more. `install_tmux()` verifies with `tmux_runs` instead of `tmux_present`. The
repair runs even under `auto_install=False`, because that flag declines to touch the
machine's packages and a loader path installs nothing.

**`main.py`** — the launch preflight asks `tmux_runs()`, and a tmux established unusable
degrades to bare mode with the soname in the notice. It is resolved **after input
validation and before anything is created**, the same ordering the client/server
version-mismatch refusal already uses — and it is skipped for a remote dispatch.
`ai doctor` applies the repair and prints the `export` line for shells outside `ai`.

**`setup.py`** — install time verifies tmux by executing it, alongside the existing
direnv bootstrap, and never fails the install over it.

**Why self-healing rather than a persistent mutation.** The repair is re-derived on every
`ai` invocation from directories that survive a restart, so the next launch after any
future rebuild fixes itself with no human action. Nothing is written to the operator's
shell profile, `~/.local/bin`, or the tmux binary. The cost is that a bare `tmux` typed
into a shell outside `ai` stays broken, which is why `ai doctor` prints the exact
`export` line for anyone who wants it permanent.

## Verification

- `tests/test_native_deps.py` — 24 tests driving a **real child process through a real
  dynamic loader**: a fake binary that succeeds only when the library is genuinely
  reachable, so no test can pass on a repair that merely claims to have worked. Covers
  rollback on a failed repair, the three loader wordings, nested payload discovery,
  symlinked prefixes, and the Windows no-op.
- `tests/test_tmux_loader_repair.py` — 22 tests on the tmux decisions and the launch:
  a broken tmux resolves the plan to `bare` and names the library; a repairable one stays
  under tmux (the anti-vacuity control, since an implementation that always chose bare
  would otherwise pass); an ambiguous non-answer is *not* called unusable; `--bare` reaches
  no probe at all; install time is verified and never fatal; `ai doctor` reports both
  outcomes.
- **Repo hard gate**: `ruff check`, `ruff format --check`, and the full suite —
  **2749 passed, 18 skipped, 0 failed**.
- **End to end on the affected host**: `ensure_tmux()` → `installed=True, tool='loader-path'`,
  `tmux_runs()` flips `False → True`, and a child process inheriting the repaired
  environment runs `tmux 3.7c`. `ai doctor` reports `OK tmux` with the `export` line, and
  `ai c 99 --dry-run` resolves `mode  local, tmux` where it previously died.
- **AI-CLI-i2ih AC-5, the stated metric**: the real-tmux tests that had been skipping on
  this host now **run**, 0 skipped. `tests/conftest.py::tmux_runnable` applies the
  production repair before giving up, which is also the only way those tests observe the
  repair against a live tmux server rather than a fixture.

<!-- /doc:region name="options" -->

<!-- doc:region name="lessons" kind="replaceable" -->

## Lessons Learned

1. **A diagnostic that nothing consults is not a check.** `probe().runs` was correct, was
   printed, and was ignored, so the report and the decision disagreed for an entire
   release. When a fact is worth printing at launch, find the branch that acts on it — or
   accept that the print is decoration.
2. **For a native binary, "installed" means "executes".** `shutil.which` stats a file and
   checks a bit; the failure happens later, in the loader. Every predicate that gates
   behaviour on a C binary has to run it.
3. **Resolve degradations before the expensive irreversible step, not after.** The
   operator paid for a worktree checkout and *then* learned tmux could not host a
   session. The version-mismatch refusal added earlier had already established this
   ordering; this preflight simply had not been moved to match.
4. **A skipped test is an unmeasured test, and its own contents rot.** Two genuine
   defects were sitting in `test_session_launch_integration.py` — a duplicated `check=`
   kwarg that raised `TypeError`, and a passthrough that recursed into its own mock. Both
   were latent for as long as the module skipped on hosts without a runnable tmux, and
   both surfaced the moment the skip lifted. Present at base commit `ac7a75d`.
5. **Discovery beats vendoring when the artefact is already local.** The alternative was
   shipping copies of system libraries and owning their staleness per architecture. The
   library was three directories away the whole time.

<!-- /doc:region name="lessons" -->
