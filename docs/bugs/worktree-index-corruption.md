---
title: "[AI-CLI-70] Git worktree index corruption — hundreds of D/untracked changes after rebase"
category: bugs
tags: [bug, git, worktree, recurring]
status: investigating
severity: P1
task: AI-CLI-70
---

# [AI-CLI-70] Git worktree index corruption

**Status:** investigating

**Severity:** P1

**Created:** 2026-04-27

**Task:** `AI-CLI-70`

## Symptoms

After a `git pull --rebase` with a merge conflict (or any failed/partial rebase), the worktree
shows hundreds of changes that don't reflect actual disk state:

```text
D  .claude/agents/analyst.md
D  .claude/hooks/debug-mode-gate.sh
D  docs/roadmap/master-roadmap.md
... (100+ more D lines)
?? .claude/
?? docs/
?? src/
?? tests/
... (100+ more ?? lines)
```

The `D` entries mean "staged for deletion" in the index. The `??` entries are the same files showing
up as untracked because the index no longer knows about them. Files physically exist on disk and are
correct — only the index is wrong.

`git diff --name-status HEAD` shows the same pattern: hundreds of deletions that aren't real.

## Environment

- macOS, git worktree at `.worktrees/ai-cli-1` on branch `wt-ai-cli-1`
- Worktree shares object store with main checkout at `~/projects/ai-cli-utils`
- Worktree has its own index at `.git/worktrees/ai-cli-1/index`
- pre-commit hooks run `git stash` before checks, `git stash pop` after

## Reproduction Steps

1. Have uncommitted changes in the worktree
2. Run `git pull --rebase` that encounters a conflict
3. Manually edit the conflict, `git add <file>`, `git rebase --continue`
4. Run `git status` — observe hundreds of D + ?? lines

## Root Cause Analysis

**Proximate cause:** The worktree index (`.git/worktrees/ai-cli-1/index`) gets into an intermediate
state during a rebase conflict resolution. When `git rebase` pauses on a conflict, it holds the
index in a partial state. If the conflict resolution or `--continue` step doesn't complete cleanly
(e.g., runs in a background shell that loses the rebase state variable, or the index flush is
interrupted), the index ends up tracking a different tree than what's on disk.

**Why it recurs — the stash cycle:**

Once the index is corrupt, the pre-commit hook's `git stash push` captures the corrupt index state
as a stash. Then `git stash pop` after the hook restores that corruption. This creates a cycle:

```text
corrupt index → push triggers stash → stash captures corruption → pop restores corruption → next push triggers stash...
```

This is why `SKIP_TESTS=1 git push` was needed to break the cycle on a prior occurrence — it
bypasses the stash entirely.

**Why this worktree is especially prone:**

The worktree's rebase operations run via `git push origin HEAD:main` which triggers the pre-push
test gate hook. The test gate runs in the same shell but the rebase conflict resolution happens
mid-command. When I resolved a conflict in `docs/roadmap/master-roadmap.md` (AI-CLI-69 vs AI-CLI-66
concurrent pushes), the rebase left the index in an intermediate state that the subsequent
`--continue` didn't fully reset.

## Prior Fix Attempts

| # | Date | What was tried | Outcome |
|---|------|----------------|---------|
| 1 | 2026-04-21 | `git read-tree HEAD && git update-index --refresh` after initial corruption | Fixed that instance; stash cycle also needed `SKIP_TESTS=1` to break |
| 2 | 2026-04-27 | `git read-tree HEAD && git update-index --refresh` before push | Fixes index but root cause (rebase in background shell + stash cycle) not addressed |

## Fix

### Immediate recovery (run when corruption is detected)

```bash
# Sync index to HEAD, refresh timestamps
git -C /path/to/worktree read-tree HEAD
git -C /path/to/worktree update-index --refresh

# Verify clean
git -C /path/to/worktree status --short
```

If corruption persists through the next push (pre-commit stash restores it):

```bash
# Break the stash cycle by bypassing the hook once
SKIP_TESTS=1 git -C /path/to/worktree push origin HEAD:main

# Then re-run with hooks on next push — should be clean
git -C /path/to/worktree push origin HEAD:main
```

### Root cause fix (to prevent recurrence)

Two changes needed:

**1. Never run conflict resolution in a background shell.**

When `git pull --rebase` produces a conflict, always resolve it in the foreground worktree shell,
not via chained `&&` commands that might run in a detached context. The rebase state is
session-scoped and doesn't survive shell subprocess boundaries cleanly.

**2. Add a pre-push guard that detects and aborts on index corruption.**

Add a check to `.githooks/pre-push` (or as a separate pre-commit hook) that aborts if the
staged-deletion count exceeds a threshold:

```bash
deleted_count=$(git status --porcelain | grep -c '^D')
if [ "$deleted_count" -gt 50 ]; then
  echo "ERROR: index corruption detected ($deleted_count staged deletions)."
  echo "Run: git read-tree HEAD && git update-index --refresh"
  exit 1
fi
```

This prevents pushing a corrupt index as an actual commit and forces manual recovery first.

**3. Add `git status` check to the session startup checklist.**

The CLAUDE.md session startup already says "check git status — ensure clean working tree". Enforce
that the check specifically looks for the D+?? pattern and runs the recovery command if found,
before any other work begins.

## Verification

- [ ] `git status --short` shows no D or ?? lines after recovery
- [ ] Push completes with hooks enabled (no `SKIP_TESTS=1` bypass)
- [ ] Next rebase conflict resolution (simulated) does not re-corrupt the index
- [ ] Pre-push guard hook catches corruption before it can be committed

## Lessons Learned

- The worktree index is a separate file from the main checkout index — it's more fragile because
  fewer git operations are designed with worktrees in mind
- `git stash` in hooks is dangerous when the index is in an intermediate state — it captures and
  preserves the corruption across pushes
- Rebase conflict resolution must happen in a foreground, stateful shell context — not chained
  background commands
- The pattern `D` (many) + `??` (same files untracked) is the diagnostic signature: run
  `git read-tree HEAD && git update-index --refresh` immediately
