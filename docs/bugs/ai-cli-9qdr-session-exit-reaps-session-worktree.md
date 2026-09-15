---
title: "The session exit trap deleted the session's own worktree, which the ratified worktree rule forbids"
category: bugs
tags: [session, worktree, git, teardown, exit, canonical]
status: fix-deployed
severity: P1
related_docs:
  - docs/bugs/ai-cli-2139-session-exit-leaves-stopped-process.md
  - docs/bugs/worktree-base-not-origin-main.md
  - docs/bugs/worktree-path-namespace-collision.md
---

<!-- doc:region name="summary" kind="replaceable" -->

# The session exit trap deleted the session's own worktree, which the ratified worktree rule forbids

**Status:** fix-deployed

**Severity:** P1 — not data loss, but a long-lived session home disappeared without a word.
Every conventionally named session worktree in the fleet was exposed, and a resumed session
was left pointing at a working directory that no longer existed.

**Created:** 2026-09-15

**Task:** `AI-CLI-9qdr`

## Symptoms

Exiting a Claude Code session launched by `ai c` deleted that session's git worktree. The
report was that it "disappeared from the VS Code Source Control extension", which turned out
to understate it: the worktree was genuinely gone, not merely hidden.

Measured on session `kg-1` in `bms-semantic-knowledge-graph`, immediately after a deliberate
clean exit:

| Observation | Result |
|---|---|
| `.worktrees/kg-1` on disk | gone |
| `.git/worktrees/kg-1` admin entry | gone |
| both parent directory mtimes | `2026-09-15 01:37:35`, the same second |
| `git worktree list` | 4 entries, `kg-1` absent |
| `git worktree prune --dry-run` | empty, so nothing was orphaned |
| branch `wt-kg-1` | alive at `4192690` |
| `.worktrees/.kg-1.lock` | still present, dated Aug 12 |

Nothing was lost. `wt-kg-1` was identical to `origin/sergei/dev-workspace`, 0 ahead / 0
behind, and contained in that remote ref, so every commit was safe. The session's 573 Claude
Code transcripts were untouched, because those live under `~/.claude/projects` rather than in
the worktree.

Two details in that table did the diagnostic work. Both halves vanishing in the same second
with nothing left to prune rules out a plain `rm -rf`, which would have orphaned the admin
entry. And the branch surviving is the specific fingerprint of `git worktree remove`, which
never touches one.

<!-- /doc:region name="summary" -->

## Causal mechanism

`src/ai_cli/session_script.py`, in the supervisor teardown trap `_supervisor_cleanup()`:

```bash
ai internal cleanup-worktree "$ai_name" 2>/dev/null
```

`src/ai_cli/main.py` dispatched that action to `cleanup_worktree(ai_name)` in
`src/ai_cli/session.py`, which resolved `<repo_root>/.worktrees/<ai_name>`, ran
`git status --porcelain`, and on empty output ran `git worktree remove` on it. No `--force`,
and the branch was never touched.

### Why it had never been seen before

A trap fires only on a graceful exit. `kg-3` and `kg-4` were also porcelain-clean at the time
of measurement and their worktrees survived, because those sessions never exited through the
trap — a killed process or a space restart skips it entirely. `kg-1` was the first session
deliberately exited while clean, so it was the first to meet both conditions at once.

This is therefore not a regression. The call dates to `121c9c9`, 2026-04-19. It sat dormant
for roughly five months because the triggering combination had not occurred.

### Why it is a defect rather than intended hygiene

ai-harness `docs/procedures/worktree-workflow.md` states the ratified rule verbatim:

> **NEVER delete a canonical `ai c`/`ai g` session worktree.** Those are long-lived session
> homes, not disposable scratch space, and they are never a cleanup candidate whatever their
> merge status or cleanliness.

Cleanliness was precisely the condition this teardown used to justify deleting one.
ai-harness's own `scripts/worktree_audit.py` implements a hard `KEEP_CANONICAL_SESSION` veto,
evaluated before any other classification question, specifically so its audit path cannot do
this. So two components of one fleet implemented opposite policies on the same object, and
the rule lived in a different repository from the violating code, which is how they drifted
apart without anyone noticing.

Exposure was fleet-wide, not specific to `kg-1`. ai-harness's `is_canonical_session()`
returns True for `kg-1`, `kg-2`, `kg-3`, `aih-4` and `ai-cli-1`.

`git status --porcelain` was also the reap's only safety test. It reports uncommitted and
untracked files but says nothing about whether commits exist anywhere else, so a worktree
whose branch carried commits present on no remote was reaped as well. The branch kept those
commits reachable, so this was recoverable rather than destructive, but the checkout was not.

## Rejected hypotheses

| Hypothesis | Why it was rejected |
|---|---|
| A stray `rm -rf`, or a crash mid-write | `git worktree prune --dry-run` was empty and the admin entry was gone too. A directory removal outside git leaves an orphaned entry that prune reports. |
| ai-harness `worktree_audit.py --delete` swept it | That path deletes the branch alongside the worktree, and it carries a canonical veto that refuses `kg-1` outright. `wt-kg-1` survived. |
| A session-end hook in the harness | The removal is fully explained by a call site inside ai-cli-utils, reproduced end to end below. No harness hook was involved. |
| A new regression from a recent change | `git log -S` on both call sites returns only `121c9c9` (2026-04-19). The code had not changed. |
| It only affects oddly configured worktrees | Reproduced on a clean scratch repo with nothing unusual, and for three different canonical session names. |

## The RED test

`tests/test_session_worktree_survives_exit.py`, 6 tests. Confirmed failing on the unfixed
code for the right reason before any production edit — every failure read
`the session worktree was deleted on session exit`.

The tests use real git, a real filesystem and the real CLI dispatcher. Nothing is mocked at
the boundary the defect lived on, and that choice is load-bearing: the six tests this fix
deletes (`TestCleanupWorktree`) mocked `subprocess.run` wholesale and asserted that
`git worktree remove` *was* called. They were green throughout. They tested the wiring, not
the behaviour, so they described the bug as the specification.

Coverage:

1. the rendered session script contains no worktree-removal command at all (the negative
   constraint, asserted against the real generated artifact bash executes);
2. a clean session worktree still exists, is still registered, and keeps its admin entry
   after teardown (the positive contract, and the exact `kg-1` scenario);
3. a worktree whose branch holds commits present on no remote also survives (the severity
   case that porcelain-clean concealed);
4. the same for `kg-1`, `aih-4` and `ai-cli-1`, so the assertion is about the class of
   canonical session names rather than one instance.

### A false-pass caught while writing them

The unpushed-commit test first used `git log --oneline --not --remotes` to prove its own
precondition, and that returned empty. Supplying `--remotes` counts as a rev argument, which
suppresses the implicit `HEAD` default, so the command reports nothing regardless of what is
actually unpushed. As a precondition check it would have passed silently while proving the
opposite of what it claimed. It now names `HEAD` explicitly, and the fixture pushes `main` to
a real bare origin so "exists on no remote" is a genuine condition rather than a vacuous one.

## The patch

Removed, together:

- the `ai internal cleanup-worktree` line from `_supervisor_cleanup()`;
- the `cleanup_worktree` function, replaced by a comment recording why nothing may take its
  place;
- the `cleanup-worktree` dispatch branch and its import in `main.py`;
- the eight tests that pinned the removed behaviour.

The `repair_bare_worktree_config(repo_root)` backstop inside the deleted function went with
it. That is deliberate and not a dropped safeguard: it was there because `worktree remove` is
a documented trigger for the `core.bare`/`core.worktree` corruption class, so with no removal
there is no trigger. Its many other call sites in `session.py`, `main.py` and
`copier_update.py` are untouched.

`copier_update.py`'s own `_cleanup_worktree` is a different function and is left alone. It
reaps a throwaway worktree that the template-update flow itself created, which is legitimate.

## GREEN results

| Ring | Result |
|---|---|
| the 6 frozen regression tests | 6 passed |
| revert production code only, tests untouched | 6 failed |
| restore the fix | 6 passed, tree clean |
| nearby suites (`test_session`, `test_cli`, `test_session_worktree_survives_exit`, `test_copier_update`, `test_git_repair`) | 425 passed, 1 skipped |
| `ruff check` / `ruff format --check` | passed, 130 files already formatted |

The revert ring was run against the committed fix rather than an uncommitted working tree,
because `git checkout --` restores from the index and would otherwise have destroyed the fix
being tested.

## Prevention lesson

**A rule ratified in one repository does not constrain code in another.** The canonical-session
protection was written down, enforced by a dedicated veto in the audit script, and still
violated for five months by a launcher in a different repository that had simply never been
told. When a policy names a class of object, every component that can destroy that object
needs the check, not just the component the policy was written for.

The second lesson is about the tests. `TestCleanupWorktree` had six passing tests over this
exact function and caught nothing, because mocking `subprocess.run` turns "what does git do"
into "what did we ask git to do". A test that mocks the boundary the defect lives on can only
ever confirm the author's intent.
