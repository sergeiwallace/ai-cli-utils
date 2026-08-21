---
title: "An empty session-worktree slot blocks every launch, and the launcher adds worktrees to the wrong repository"
category: bugs
tags: [session, worktree, git, windows, launch]
status: fix-deployed
severity: P1
related_docs:
  - docs/bugs/worktree-base-not-origin-main.md
  - docs/bugs/worktree-index-corruption.md
  - docs/bugs/ai-cli-8s1-session-adopt-worktree-slug.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# An empty session-worktree slot blocks every launch, and the launcher adds worktrees to the wrong repository

**Status:** fix-deployed

**Severity:** P1 — a repository in this state cannot host a session at all. The refusal is
permanent: nothing in the tool clears the directory, so every subsequent launch fails the
same way until a human intervenes.

**Created:** 2026-08-21

**Task:** `AI-CLI-ai-c-1-msxj` (hash `AI-CLI-pcv`), P1 bug, "ai c 1 — stale orphan worktree
directory blocks launch on Windows". The diagnosis below could not be written into the issue:
this project's Beads store is broken on this machine (see Lessons Learned).

Two defects, filed together because the second was found by verifying the first end to end.
They are independent — neither causes the other.

## Symptoms

### D1 — the reported failure

`ai c 1` from a repository root, with no session running and a clean working tree:

```text
Error: could not create or reuse the isolated session worktree; refusing to launch in the
repository root. create_worktree: <repo>\.worktrees\aih-1 exists but is not a worktree of
<repo> — refusing to delete it. Remove or relocate it only after verifying it contains no
needed files; if it is empty, run `rmdir <repo>\.worktrees\aih-1` and re-run. Re-run after
resolving the git worktree error, or explicitly use --no-worktree.
```

The directory named in the error was empty. The error told the human to remove a path that
`git worktree add` would have written into unaided, and the advice it gave (`rmdir`) was the
launcher's own job.

Two slots were in this state in the same repository (`aih-1`, `kg-1`), so the state recurs
rather than being a one-off.

### D2 — no symptom at the point of failure

`create_worktree(..., repo_root=X)` called from a checkout of a *different* repository
registers the worktree, and creates the `wt-<name>` branch, in the repository the caller is
standing in. git still writes the checkout to the requested path, so the directory exists and
holds real files. The launch dies several steps later:

```text
RuntimeError: create_worktree: failed to set upstream=origin/main on branch 'wt-aih-1' after
retry (AI-CLI-128 …). git stderr: fatal: branch 'wt-aih-1' does not exist
```

A message about a branch, with nothing pointing at the repository mix-up that caused it.

## Environment

- Windows 11, Git Bash, git 2.55.0.windows.3
- ai-cli-utils at base commit `6aea208`
- Reported from a repository root with a clean tree and no session running

## Reproduction Steps

### D1

```sh
cd <repo>                        # any repo with an `origin` remote
mkdir -p .worktrees/session-1    # an empty slot, unregistered
ai c 1                           # refuses
```

Deterministic. The equivalent at the API boundary: `create_worktree("session-1")` raises
`RuntimeError`.

### D2

```sh
cd <other-repo>
python -c "from pathlib import Path; from ai_cli.session import create_worktree; \
           create_worktree('session-1', repo_root=Path('<repo>'))"
```

Deterministic. `<other-repo>` gains a worktree and a `wt-session-1` branch; `<repo>` gains
neither.

## Root Cause Analysis

### D1: the refusal never implemented its own stated contract

Causal chain:

1. **Trigger** — `.worktrees/<name>` exists on disk and is not registered as a worktree of
   this repository, holding no entries.
2. **Incorrect decision** — `create_worktree` tested `if wt_dir.exists():`, treating "exists"
   as "occupied". It never tested emptiness.
3. **Propagation** — `_contains_git_checkout` returns `None` for an empty directory, so the
   nested-checkout branch is skipped and control reaches the unconditional refusal.
4. **Symptom** — `RuntimeError`, which `main.py` renders as a launch refusal.

The guard was introduced in `23e8796` (2026-08-10), which replaced an unconditional
`shutil.rmtree` of the colliding directory with a refusal. **That commit's own message scopes
the refusal to a non-empty directory** — "an unregistered, non-empty directory is always
refused with remediation guidance, never auto-deleted" — but the code it shipped drew no such
distinction. So this is a regression in `23e8796` against its own stated contract, not a
deliberate choice: before it, an empty slot was deleted and recreated and the launch worked.

The refusal is right for every other case. An empty directory is the one case with nothing to
protect, and it is also the one case git accepts.

**Measured — git's contract for an existing path (git 2.55):**

| Existing path at the target | `git worktree add` |
|---|---|
| empty directory | **succeeds**, exit 0, writes the checkout into it |
| directory holding one dotfile | `fatal: '<path>' already exists`, exit 128 |
| directory holding one empty subdirectory | `fatal: '<path>' already exists`, exit 128 |
| directory holding a regular file | `fatal: '<path>' already exists`, exit 128 |

git's emptiness test is therefore one level deep and counts every entry — this is git's own
`is_empty_dir`. The fix has to agree with it exactly: a looser reading (recursively empty,
or ignoring dotfiles) would admit a slot git then rejects, replacing an actionable refusal
with a confusing one.

**Where the empty directory comes from — `git worktree remove` is not atomic on Windows.**
Measured directly, holding one file in the worktree open the way a live process does:

```text
remove exit:       255
stderr:            error: failed to delete '<path>/.worktrees/slot': Invalid argument
dir still exists:  True
dir contents:      ['f.txt']
git worktree list: <main tree only>   ← already deregistered
```

Registration is dropped *before* the directory removal is verified. Once the leftover files
clear, the slot is empty and deregistered — exactly the observed state. `cleanup_worktree`
passes `check=False` and ignores the exit code, so nothing reports it. This is a contributing
producer, not the defect: an empty slot also arises from an interrupted `git worktree add`, a
backup or antivirus tool, or a human `mkdir`, and the consumer must handle it regardless. See
Lessons Learned for the follow-up.

### D2: the only git call that writes ignored `repo_root`

`create_worktree` takes an explicit `repo_root` and passes `cwd=repo_root` to every git call
that *asks* a question — `git worktree prune`, `git worktree list` via
`registered_worktrees`, and the branch-resolution helpers. The two `git worktree add` calls —
the only ones that *write* — passed no `cwd`, so git resolved the repository from the
process's current directory.

Nothing detects it. The path argument is absolute, so the checkout lands where asked; only the
registration and the branch go elsewhere. The resulting artifact is a worktree registered in
one repository pointing into another, invisible to any later launch of either.

`ai c` never hit this because it launches from the repository root, where cwd and `repo_root`
agree. **`ai session-adopt` is the exposed caller:** `session_adopt.py:677` passes `repo_root`
resolved from a recorded transcript, so it can run from anywhere.

Confirmed pre-existing at base `6aea208`: neither `add` call carries `cwd` there.

## Prior Fix Attempts

| Hypothesis | Predicted observation | Check | Result |
|---|---|---|---|
| D1 is a producer bug — something leaves empty slots, so fixing the consumer is symptom-patching | No empty slot would exist if teardown were correct | Measured `git worktree remove` under a held file handle; read `cleanup_worktree` | **Partly confirmed, rejected as the root cause.** A producer bug is real (see above), but an empty slot also has causes outside this tool, and the refusal contradicts its own documented contract regardless. Fixing only the producer would leave the launch blocked. |
| D1 is intended behaviour, protecting unknown content | The commit that added it would say so | Read `23e8796`'s message and diff | **Rejected.** The commit scopes the refusal to a *non-empty* directory; the code overshot it. |
| An existing test pins the empty-slot refusal, so the contract was deliberate | Some test asserts a raise on an empty slot | Read all 12 refusal/reuse tests in `test_worktree_container_collision.py` | **Rejected.** Every one uses a non-empty slot (a file, a `.git`, a nested worktree). The empty case was untested — which is why the overshoot shipped. |
| D2 is an artifact of the verification harness, not a production defect | No production caller passes `repo_root` from a foreign cwd | Grepped callers | **Rejected.** `session_adopt.py:677` does exactly that. |

## Fix

**D1** — `src/ai_cli/session.py`, commit `0165e25`. `_is_empty_dir` added, mirroring git's
`is_empty_dir` (single level, every entry counts; anything that is not a readable directory
answers `False`, so the caller falls through to refusing rather than to deleting). The guard
becomes `if wt_dir.exists() and not _is_empty_dir(wt_dir):`. An empty slot now falls through
to the existing `git worktree add`, which writes into it. Nothing is deleted, and no other
case changes.

**D2** — `src/ai_cli/session.py`, commit `c1e0c8b`. `cwd=repo_root` added to both
`git worktree add` calls, matching every other git call in the function.

## Verification

D1, in widening rings:

1. RED confirmed before any production edit — `session.py:1123`, the reported `RuntimeError`.
2. Fix applied; the file's 15 tests pass (3 new, 12 pre-existing, none weakened).
3. Guard reverted in isolation → RED again at the same assertion. Restored → GREEN.
4. `tests/test_worktree_container_collision.py tests/test_session.py
   tests/test_session_launch_locality.py` → 167 passed, 3 skipped.
5. Real end-to-end against the reporting repository: `create_worktree("aih-1",
   repo_root=<ai-harness>)` on the real empty slot. Unpatched source raised the reported
   error; patched source passed the guard and reached `git worktree add`. This is what
   exposed D2.
6. `ruff check` and `ruff format --check` clean on both changed files.

D2:

1. RED confirmed before the edit, with the same `fatal: branch 'wt-session-1' does not exist`
   the live end-to-end produced.
2. Defect confirmed pre-existing at base `6aea208` by reading that revision's source.
3. Fix applied; `test_worktree_repo_root_targeting.py`,
   `test_worktree_container_collision.py`, `test_worktree_base.py` → 21 passed.

Regression tests, all against real repositories and real git — no mock at either causal
boundary:

- `test_given_an_empty_unregistered_directory_when_a_session_worktree_is_created_then_it_is_reused`
- `test_given_a_slot_holding_only_a_dotfile_when_a_session_worktree_is_created_then_it_is_refused`
- `test_given_a_slot_holding_only_an_empty_subdirectory_when_a_session_worktree_is_created_then_it_is_refused`
- `test_given_cwd_in_another_repository_when_created_with_repo_root_then_the_worktree_is_added_to_repo_root`
- `test_given_cwd_in_another_repository_when_created_with_repo_root_then_that_repository_is_left_untouched`

The two D1 refusal tests are not redundant coverage: they pin the fix to git's exact
emptiness contract and reject the shallow variants (swallow the refusal and return the path;
read emptiness recursively; go back to rmtree-and-recreate). The D2 pair asserts registration
and branch rather than the directory, because the checkout appears at the requested path on
the buggy code too and `is_dir()` cannot discriminate.

## Lessons Learned

**A guard that refuses more than its own commit message claims is a guard nobody reviewed
against its contract.** `23e8796` stated "non-empty" three times in prose and never encoded
it. The reason it survived three months: every test written for that commit used a non-empty
directory, so the untested case was the one the prose had excluded. When a refusal is scoped
in prose, the scope boundary is the test that matters most.

**Remediation advice the tool could have followed itself is a defect report.** The error text
said "if it is empty, run `rmdir <path>` and re-run" — the launcher knew the directory might
be empty, knew what to do about it, and asked a human to do it. That sentence was the bug,
written down, shipped, and read by everyone who hit it.

**Verifying against the real reporting repository, not only fixtures, is what found D2.** The
fixture suite passed completely with D2 live, because every test `chdir`s into its repository
first. Only the real end-to-end — where cwd was a different project — separated `repo_root`
from cwd, which is the exact condition `ai session-adopt` runs in.

**Windows teardown is non-atomic, and `check=False` hides it.** `cleanup_worktree` ignores a
failed `git worktree remove`, so a partially-removed worktree is left silently and the next
launch inherits it. Not fixed here: making teardown report or retry is a separate behavioural
decision, not part of this causal fix. Follow-up, unfiled for the same store reason as above:
*`cleanup_worktree` ignores a failed `git worktree remove`, leaving a deregistered directory
with no diagnostic.*

**This project's Beads store is unusable on this machine, so neither the diagnosis nor the
follow-up could be filed.** `.beads/embeddeddolt/AI_CLI/.dolt/` holds `noms/` and `temptf/`
but no `repo_state.json` — a half-initialised store, matching a `bd-new` that was killed at
its timeout on 2026-08-17. Every `bd` call fails with `failed to open database`. This document
is the record of both defects until the store is repaired.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

### 2026-08-21 — empty slot reused instead of refused (`0165e25`)

`src/ai_cli/session.py` (`_is_empty_dir` added; the `wt_dir.exists()` guard narrowed).
Tests: `tests/test_worktree_container_collision.py` (3 new, against real repositories).

Diagnosed and fixed in-session rather than delegated: the discriminating evidence was a live
measurement of git's own behaviour on three directory shapes plus a read of the commit that
introduced the guard, and the fix is one predicate at one call site. Step 2.5 scope check —
one file, one cohesive component, no public contract change, no new abstraction, reversible:
narrow causal fix, no redesign route.

### 2026-08-21 — worktree add targets repo_root, not the caller's cwd (`c1e0c8b`)

`src/ai_cli/session.py` (`cwd=repo_root` on both `git worktree add` calls). Tests:
`tests/test_worktree_repo_root_targeting.py` (new, 2 tests against two real clones).

Found by the preceding fix's end-to-end verification, confirmed pre-existing at `6aea208`,
landed as its own atomic commit per "fix what you find".

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

### Evidence — git's contract for an existing target path (git 2.55.0.windows.3)

```text
--- git worktree add into EXISTING EMPTY dir ---
Preparing worktree (new branch 'wt-empty')
HEAD is now at a9d308d init
exit=0
--- result ---
f.txt                                    ← the checkout landed in the pre-existing dir
--- now a NON-empty existing dir ---
fatal: '.worktrees/full-slot' already exists
exit=128
--- dir containing only an EMPTY SUBDIR ---
fatal: '.worktrees/subdir-only' already exists
exit=128
--- dir containing only a DOTFILE ---
fatal: '.worktrees/dotfile-only' already exists
exit=128
```

### Evidence — `git worktree remove` deregisters before it verifies deletion (Windows)

One file inside the worktree held open by another process:

```text
remove exit: 255
stderr: error: failed to delete '<tmp>/repo/.worktrees/slot': Invalid argument
dir still exists: True
dir contents: ['f.txt']
--- worktree list ---
<tmp>/repo 09513ae [master]              ← the worktree is already gone from the list
```

### Evidence — the reported repository, before the fix

```text
before: exists = True | entries = []      ← the slot is genuinely empty
before: registered = ['<ai-harness>']
RuntimeError: create_worktree: <ai-harness>\.worktrees\aih-1 exists but is not a worktree of
<ai-harness> — refusing to delete it. …
```

### Evidence — D2, measured on the real repositories

`create_worktree("aih-1", repo_root=<ai-harness>)` run from a checkout of this project. From
this project's repository root afterwards:

```text
$ git worktree list
<ai-cli-utils>                       6aea208 [main]
<ai-cli-utils>/.worktrees/ai-cli-1   6aea208 [wt-ai-cli-1]
<ai-cli-utils>/.worktrees/ai-cli-2   6aea208 [wt-ai-cli-2]
<ai-harness>/.worktrees/aih-1        6aea208 [wt-aih-1]   ← another project's path

$ cat <ai-harness>/.worktrees/aih-1/.git
gitdir: <ai-cli-utils>/.git/worktrees/aih-1

$ git -C <ai-harness> worktree list
<ai-harness> 9fcf5c31 [main]                              ← the target knows nothing about it
```

<!-- /doc:region name="appendix_evidence" -->
