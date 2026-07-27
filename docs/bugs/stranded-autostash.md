---
title: "[AI-CLI-132] git pull --rebase --autostash exits 0 when its stash pop conflicts"
category: bugs
tags: [bug, git, autostash, silent-failure, deployment]
status: fix-deployed
severity: P0
task: AI-CLI-132
---

# [AI-CLI-132] Stranded autostash — a lying exit code

**Status:** fix-deployed

**Severity:** P0

**Created:** 2026-07-27

**Task:** `AI-CLI-132` (AIH-443 Shape B)

## Symptoms

A repo is left with conflict stages in its index and **no merge or rebase in progress**:

```text
git status -sb          ## main...origin/main [behind 50]
                        UU .gitignore
git ls-files -u         3 stages for .gitignore
git stash list          stash@{0}: autostash
.git/MERGE_HEAD         absent
.git/rebase-merge       absent
.git/rebase-apply       absent
```

Nothing reported an error. An index conflict with no operation in progress is the signature: the
rebase completed, the automatic stash pop conflicted, and the state was orphaned.

## Root cause

Two separate defects compound. Only the second is ours.

### 1. Git returns 0 when the autostash pop conflicts (git's behaviour, not a bug we own)

`git pull --rebase --autostash` reports the *pull's* success. The autostash pop happens after the
rebase concludes, and its failure is written to stderr as a warning without touching the exit code.

Measured, not read from documentation:

| git version | scenario | exit code | unmerged stages | stash left |
| ----------- | -------- | --------- | --------------- | ---------- |
| 2.55.0 (macOS) | fast-forward pull, pop conflicts | **0** | 3 | 1 |
| 2.55.0 (macOS) | rebasing pull, pop conflicts | **0** | 3 | 1 |
| 2.43.0 (Linux) | fast-forward pull, pop conflicts | **0** | 3 | 1 |
| 2.43.0 (Linux) | rebasing pull, pop conflicts | **0** | 3 | 1 |

Negative control — the same harness, same repos, but with the conflict moved into the rebase body
(two committed same-line edits) instead of the stash pop:

| git version | scenario | exit code |
| ----------- | -------- | --------- |
| 2.55.0 / 2.43.0 | rebase-body conflict | **1** |

So the harness can observe a non-zero exit; the 0 above is git's actual behaviour for this case, not
a measurement artefact. Both versions agree, so this is not a version regression.

### 2. Our callers discarded the code anyway (ours)

`src/ai_cli/main.py:1000` (pre-fix), inside `_do_update_or_deploy` — the `ai update` / `ai deploy`
path:

```python
subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=project_path, check=False)
```

The return value is not even bound. The follow-up conflict scan that does exist only reads
`src/**/*.py`, so the live incident's conflict — in `.gitignore` — fell outside it entirely.

`src/ai_cli/main.py:1584` (pre-fix), the `ai c N` worktree launch, gated on `pull.returncode != 0`,
which by defect 1 never fires. Its AIH-443 `detect_stranded_autostash` backstop reported **any**
stash entry as a strand, so it false-positived on every launch in a repo holding saved WIP —
verified against a clean pull in a repo with one pre-existing stash. The stranded box held three.
A warning that always fires is a warning nobody reads.

### 3. The latch — why it lasted five days rather than one launch

Once the index carries conflict stages, every later pull refuses:

```text
error: Pulling is not possible because you have unmerged files.
fatal: Exiting because of an unresolved conflict.        (exit 128)
```

`check=False` discarded that code too. And `_auto_update_if_stale` (`main.py:243`) writes its
stamp file keyed on the *current* HEAD **before** running the update, so with HEAD frozen the
early-return at `main.py:253` matches forever and auto-update never retries. Verified on the live
box: stamp file and HEAD were both `4711d4e`.

## Blast radius (live incident)

`sergei@178.104.70.139:~/projects/ai-cli-utils` stranded 2026-07-22 21:42:40, found 2026-07-27 —
**five days**, 50 commits behind, zero local commits at stake. During that window a runaway log grew
to 1.97 GB and filled the disk (CORE-88/CORE-89), and a shipped quota-scrape fix could not be
deployed there. One exit-0-on-failure produced a dead server.

## Fix

`git_repair.pull_rebase_autostash()` measures repo **state** either side of the pull rather than
trusting the exit code, and reports a strand when *this* pull caused one:

* unmerged index paths that were not present before, or
* a stash stack that grew across the call.

Both are deltas, so a repo the user is already resolving a conflict in, or one holding unrelated WIP
stashes, is not misattributed. A non-zero exit with an intact tree (no network, say) is not a
strand — callers stay usable offline.

Callers:

* `ai update` / `ai deploy` — **fatal** on any conflicted index, whether this run caused it or a
  previous one did. A conflicted source tree cannot be built from, and this is what breaks the latch.
* `ai c N` worktree launch — **refuses to launch** when the launch itself stranded the worktree.
  Dropping an agent into an index carrying conflict stages is how AIH-443's phantom deletions spread
  across six worktrees. A pre-existing conflict is left alone, so a session can still be launched to
  help resolve one.

Nothing is auto-repaired and no stash is ever dropped: the user's work is in the stash and only they
can say how to reconcile it.

## Proving the guard can fail

Four variants faced the identical genuine pop conflict:

| Variant | Verdict |
| ------- | ------- |
| New guard `pull_rebase_autostash()` | **CAUGHT** |
| Pre-fix gate `if pull.returncode != 0` | **MISSED** |
| Mutant: unmerged-path clause deleted | caught (via stash delta) |
| Mutant: stash-delta clause deleted | caught (via unmerged paths) |

In all four the repo really was conflicted afterwards, so "MISSED" means blind, not "nothing
happened". Separately, disabling both clauses in the shipped code turned the regression tests red,
and disabling the `ai update` fatal branch made that path exit **0** and proceed to install from the
conflicted checkout — the live incident, reproduced through the real caller.

## Related

* `docs/bugs/worktree-index-corruption.md` — AI-CLI-70, the adjacent corruption class
* AIH-443 Shape A — silently dropped tracked symlinks, `detect_missing_tracked_symlinks()`
