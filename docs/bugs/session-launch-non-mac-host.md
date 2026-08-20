---
title: "[BUG-003] Session launch broken on non-Mac hosts — wrong repo, blocked .envrc, failed update"
category: bugs
tags: [session, worktree, direnv, update, portability, linux]
status: fix-deployed
severity: P0
related_docs:
  - docs/bugs/worktree-index-corruption.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# [BUG-003] Session launch broken on non-Mac hosts — wrong repo, blocked .envrc, failed update

**Status:** fix-deployed

**Severity:** P0 — `ai c` could not start a session at all on an affected host.

**Created:** 2026-07-28

**Task:** unfiled — the `bd` binary is not installed on this machine (the `.beads/` store
is committed, but `which bd` finds nothing), so no bead could be created. Tracked as a CC
task pending Beads setup.

## Symptoms

Four distinct failures on a Linux host with `AI_HOST` set and `[session] use_tmux = false`:

1. **Session created in the wrong repository.** Running `ai c` inside `myapp` created the
   worktree, the `wt-app-1` branch, and the session under the *configured main project*
   instead:

   ```
   /…/projects/myworkspace/.worktrees/app-1
   ```

   The session name (`app-1`) was correct, so the mistake was silent — the tab looked right
   while the session was editing a different repository.

2. **No engine ever started.** The launch died with only a direnv message and exit 1:

   ```
   direnv: error /…/myworkspace/.worktrees/app-2/.envrc is blocked.
   Run `direnv allow` to approve its content
   ```

   `direnv allow`, run from the repo the user was actually in, then reported
   `direnv: error .envrc file not found` — because the blocked file belonged to the *other*
   repo's worktree, which bug 1 had created.

3. **SyntaxWarning on every launch:**

   ```
   …/site-packages/ai_cli/session_script.py:602: SyntaxWarning: invalid escape sequence '\.'
   ```

4. **`ai update` could not update.** The auto-update printed a wall of git advice and
   carried on:

   ```
   There is no tracking information for the current branch.
   Please specify which branch you want to rebase against.
   …
   Updating 0.7.0 → 0.7.0.post20260728165636
   ```

   It reported a version bump while having installed an unchanged tree.

## Environment

- Linux (SageMaker Code Editor terminal), `AI_HOST` set to a non-`mac` value
- `ai-cli-utils` 0.7.0
- `[session] use_tmux = false`; tmux not installed and not installable on this host
- `[project] projects_dir` pointed at a mounted filesystem, **not** `~/projects`
- `[project] main_project = "myworkspace"`
- direnv installed; no `.envrc` approved yet (fresh machine)
- Repos affected: every registered repo, `ai-cli-utils` itself included

## Reproduction Steps

1. Set `AI_HOST` to any value other than `mac`, and `[project] main_project` to a repo other
   than the one you will launch from.
2. `cd` into a *different* registered repo (e.g. `myapp`).
3. Run `ai c`.
4. Observe: the worktree is created under `main_project`, not the current repo
   (`git -C <main_project> worktree list` shows `app-1`), and — if any `.envrc` in that path
   is unapproved — the command exits 1 having started no engine.

Measured directly, rather than inferred:

```
$ direnv exec . echo RAN_THE_COMMAND; echo "RC=$?"
direnv: error /…/.envrc is blocked. Run `direnv allow` …
RC=1                     # and RAN_THE_COMMAND never printed
```

## Root Cause Analysis

Four independent causes; bugs 1 and 2 compounded into the observed failure.

**1 — `_resolve_is_remote()` inferred remoteness from a host name.** It returned True for
any `AI_HOST` not equal to `"mac"`:

```python
host = os.environ.get("AI_HOST", "")
return bool(host) and host not in ("mac",)
```

`--is-remote` is injected by the *local* machine when it SSHes out to launch a session, so
it means "another machine drove this launch". A host merely *having a name* says nothing
about who initiated the session. Every ordinary local launch on a Linux or Windows
workstation therefore took the `if is_remote:` branch of `_do_session_launch`, which
deliberately `chdir`s to the configured main project before any worktree work happens — so
the worktree, branch, and session all landed in the wrong repository.

This also mislabels a purely local session as `c-r-…` in the session map and `ai ls`.

**2 — `_exec_with_direnv()` treated direnv as a precondition.** It exec'd
`direnv exec <root> <engine>` unconditionally. `direnv exec` **fails closed**: on an
unapproved or erroring `.envrc` it exits 1 and never runs the command. Since `os.execvp`
replaces the process, that exit *was* the launch's exit. A trust prompt — a recoverable,
cosmetic condition — became total loss of the session. The same call also made direnv a hard
dependency for repos that have no `.envrc` at all.

**3 — invalid escape in a generated shell script.** `session_script.py` embedded the shell
regex `'direnv: error.*\.envrc is blocked'` inside a Python f-string. `\.` is not a
recognised Python escape; Python currently warns and keeps the backslash, but this is slated
to become a `SyntaxError`, so it is a latent break, not just noise on stderr.

**4 — `ai update` assumed the source tree tracks a remote.** It ran a bare
`git pull --rebase --autostash` with `check=False` and never inspected the result. The
source tree is routinely parked on an unpushed local branch (`fix/…`), where that command
aborts with "no tracking information". Swallowing the failure meant the update reinstalled
the *unchanged* tree while printing a successful-looking version bump — a stale build
indistinguishable from a fresh one.

**Why the tests did not catch bugs 1 and 2.** Every existing bare-mode launch test stubs
`_resolve_is_remote` to False:

```python
(patch("ai_cli.session._resolve_is_remote", return_value=False),)
```

which asserts the *fixed* behaviour while the shipped function returned True. Separately, 22
launch tests only passed because the checkout physically sat at `~/projects/<name>`:
`is_current_project_resolved()` reads the real filesystem, and a later-added guard runs
before the patched `get_project_prefix` is ever used. On any other layout — a mounted
volume, CI checking out to `/build`, a clone into `~/src` — they failed with
"no project resolved" for reasons unrelated to what they assert.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-07-27 (`55ace53`) | Added `[session] use_tmux` opt-out and a cross-platform tmux preflight | Necessary but insufficient — decoupled tmux, left the remote-inference and direnv faults. Also left its own test asserting the old win32-only guard. |
| 2 | 2026-07-27 (`7086960`) | Gave bare mode worktree isolation, `--name`, and resume | Fixed bare-mode ordering, but its tests stubbed `_resolve_is_remote`, hiding bug 1 entirely. |

## Fix

- **`session.py` — `_resolve_is_remote()`** now returns `is_remote_flag` only. Remoteness is
  a property of *who launched the session*, and `--is-remote` is the sole trustworthy signal.
- **`main.py` — `_exec_with_direnv()`** treats direnv as an enhancement. It resolves the
  nearest `.envrc` (searching parents, as direnv does), probes whether the environment can
  actually load, and then: no `.envrc` → exec the engine directly; usable → exec under
  `direnv exec`; blocked → warn with the exact `direnv allow <dir>` command and start the
  session *without* the project environment. A degraded session beats no session.
- **`session_script.py`** — `\.envrc` → `[.]envrc`, equivalent in ERE, valid in Python.
  Verified the regex still matches the blocked-message text and still rejects unrelated text.
- **`git_repair.py` — `_pull_refspec()`** (new) makes `pull_rebase_autostash()` pass an
  explicit `origin <branch>` when the branch has no upstream, and add nothing in a detached
  HEAD (where guessing a branch would be wrong). Placing it in the shared helper fixes both
  the `ai update` path and the worktree-sync path at once.
- **`main.py` — worktree sync warning** now quotes git's last error line. An exit code
  alone does not distinguish no-network from missing credentials, and the user cannot
  re-run the pull to find out once the index has been restored.

**Reconciled with concurrent upstream work.** While this was being fixed, `origin/main`
advanced 29 commits, two of which overlap:

- `2742783` reached the *identical* NATS root cause and fix (`max_reconnect_attempts=1`)
  independently, with better provenance (a census of 49 orphaned `ai internal publish`
  processes). Upstream's docstring was kept; this branch adds only the `asyncio.wait_for`
  deadline, because relying on the library to honour its own options is not itself a bound.
- `dde49f6` fixed the same stale tmux-guard test left behind by `55ace53`. Upstream's
  version was kept (it also covers the Homebrew hint on darwin); the duplicate added here
  was dropped.
- `a653fbd` made `create_worktree` hard-fail when a worktree branch's upstream cannot be
  set to `origin/main`. The launch-test fixtures here therefore clone from a bare remote
  instead of using a bare `git init`, so `origin/main` genuinely exists.
- **`tests/conftest.py`** — new autouse fixture points `projects_dir` at the checkout's own
  parent, fixing the hermeticity defect once at the process boundary instead of per test.

## Verification

- [x] `_resolve_is_remote` is False for a named non-Mac host, True only for the flag
- [x] `ai c` in each repo creates the worktree **in that repo** — verified live in five
      registered repos with a stub engine printing its own cwd/argv
- [x] A blocked `.envrc` still starts the engine (warning, not exit 1)
- [x] No `.envrc` present → engine exec'd directly, so hosts without direnv work
- [x] No module emits `SyntaxWarning` (suite-wide check, `tests/test_no_syntax_warnings.py`);
      guard confirmed to go red when the `\.` is reintroduced
- [x] `ai update` on a branch with no upstream pulls `origin <branch>`; a failed pull warns
      with git's reason and still installs
- [x] `CLAUDE_CODE_TASK_LIST_ID` pinned to `ai_name`; no tmux invoked in bare mode
- [x] `ruff check` + `ruff format --check` clean
- [x] Full `pytest` green: 1961 passed, 7 skipped, 0 failed (rebased on `origin/main`)
- [x] Verified with the real installed `ai` binary, not just the source tree, in all 10
      registered repos on the host
- [x] No CC session created in tmux (counted session-shaped tmux sessions before/after a
      launch: 0 → 0). Note tmux *is* installed here, so its absence could not have been
      the evidence.

## Lessons Learned

- **Never infer a machine's *role* from its name.** `AI_HOST` identifies a host; it cannot
  tell you who initiated a session. The flag that carries that meaning already existed.
- **A stub that asserts the behaviour you wish you had, hides the behaviour you shipped.**
  Patching `_resolve_is_remote` to False in every launch test made bug 1 invisible while
  reading as coverage. When a test patches the function whose contract is in question, it
  tests the patch.
- **Distinguish enhancement from precondition, and know which way a tool fails.**
  `direnv exec` fails closed and *skips the command*, so wrapping a launch in it converts any
  environment problem into total launch failure. Probing whether the wrapper can work is
  cheap; discovering it can't after `execvp` is impossible.
- **`check=False` on a command whose result you never inspect is a silent-corruption
  generator.** The update path printed a version bump for a tree it had failed to update.
- **Don't assert a cause you haven't verified.** "(autostash pop conflict?)" was a guess
  hard-coded into the warning, and it displaced the git message naming the real cause.
- **Tests must not depend on where the repo happens to live.** Fixing `projects_dir` at the
  conftest boundary is the same "fix it once at the process boundary" approach the existing
  `GIT_*` scrub already uses; per-test defence had already failed here.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

### 2026-07-28 — all four causes fixed

`src/ai_cli/session.py`, `src/ai_cli/main.py`, `src/ai_cli/session_script.py`;
tests in `tests/test_session_launch_locality.py`, `tests/test_no_syntax_warnings.py`,
`tests/test_bare_worktree.py`, `tests/test_cli.py`, `tests/test_main.py`,
`tests/conftest.py`.

### 2026-07-28 — repository names anonymised for publication (BUG-005)

This document originally named the private repositories the failure was observed in, which
this public package's naming rule forbids. Every repository and session name here is now a
generic placeholder (`myapp`/`app-1`, `myworkspace`, `mylib`, `mysite`, `myservice`). No
finding, command, output, or count was altered — including inside the immutable evidence
appendix, where only the names were substituted. See `docs/bugs/public-repo-private-names.md`.

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

### Evidence — worktree landed in the wrong repo (before the fix)

```
$ cd myapp && ai c
direnv: error /…/myworkspace/.worktrees/app-1/.envrc is blocked…

$ git -C myapp worktree list
/…/projects/myapp  940d6b8 [dev-workspace]

$ git -C myworkspace worktree list
/…/projects/myworkspace                   ce76fa8 [main]
/…/projects/myworkspace/.worktrees/app-1  ce76fa8 [wt-app-1]
/…/projects/myworkspace/.worktrees/app-2  ce76fa8 [wt-app-2]
```

### Evidence — `direnv exec` skips the command on a blocked .envrc

```
$ direnv exec . echo RAN_THE_COMMAND >out 2>err; echo "RC=$?"
RC=1
$ cat out          # empty — the command never ran
$ cat err
direnv: error /…/.envrc is blocked. Run `direnv allow` to approve its content
```

### Evidence — correct behaviour after the fix (stub engine reporting its own cwd/argv)

```
app  → STUB_CLAUDE_CWD=/…/myapp/.worktrees/app-1
       STUB_CLAUDE_ARGV=--dangerously-skip-permissions --name app-1
lib  → STUB_CLAUDE_CWD=/…/mylib/.worktrees/lib-1
site → STUB_CLAUDE_CWD=/…/mysite/.worktrees/site-1
svc  → STUB_CLAUDE_CWD=/…/myservice/.worktrees/svc-1
```

<!-- /doc:region name="appendix_evidence" -->
