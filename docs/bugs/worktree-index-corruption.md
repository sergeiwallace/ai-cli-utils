---
title: "[AI-CLI-70] Git worktree index corruption — hundreds of D/untracked changes after rebase"
category: bugs
tags: [bug, git, worktree, recurring]
status: fix-deployed
severity: P1
task: AI-CLI-70
---

# [AI-CLI-70] Git worktree index corruption

**Status:** fix-deployed

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
| 3 | 2026-04-27 | Added pre-push guard hook (`.githooks/corrupt-index-guard.sh`) to detect corruption and abort | Prevents pushes, but doesn't break the autostash cycle — corruption persists across sessions |

## Root Cause Analysis — Round 2 (2026-04-27)

The pre-push guard from Round 1 catches corruption at push time but doesn't prevent it from
re-appearing at the next session. The mechanism is `git pull --rebase --autostash` in session startup:

1. Session ends with index in corrupt state (D entries from prior rebase conflict)
2. New session starts → `ai c N` runs `git pull --rebase --autostash`
3. `--autostash` runs `git stash push`, which saves the corrupt index state as a stash entry
4. Rebase runs on a now-clean index (stash captured the corruption)
5. `git stash pop` restores the stash → corrupt index is back
6. Repeat every session

This is the **autostash perpetuation cycle**. The corruption is preserved across session boundaries
via the autostash mechanism, not via any manual stash or hook-triggered stash.

## Fix

### Immediate recovery (run when corruption is detected)

```bash
git read-tree HEAD && git update-index --refresh && git status --short
```

### Round 1 — Pre-push guard to block pushes on corrupt index

`.githooks/corrupt-index-guard.sh` + `.pre-commit-config.yaml` (`stages: [pre-push]`).

Counts staged deletions via `git diff --cached --diff-filter=D`. Aborts if >50 (configurable via
`CORRUPT_INDEX_THRESHOLD`). Bypass: `SKIP_INDEX_GUARD=1`.

Tests: `tests/hooks/test-corrupt-index-guard.sh` — 16/16 passing.

### Round 2 — Self-healing in `ai c` session startup (breaks the autostash cycle)

In `main.py`, immediately before `git pull --rebase --autostash`, detect and fix corruption:

```python
_deleted = subprocess.run(
    ["git", "diff", "--cached", "--name-only", "--diff-filter=D"],
    capture_output=True, text=True, cwd=worktree_path,
)
if len(_deleted.stdout.strip().splitlines()) > 50:
    subprocess.run(["git", "read-tree", "HEAD"], capture_output=True, cwd=worktree_path)
    subprocess.run(["git", "update-index", "--refresh"], capture_output=True, cwd=worktree_path)
    print(f"Info: index corruption auto-healed ...", file=sys.stderr)
```

This ensures `--autostash` captures a clean index rather than the corrupt one, breaking the
perpetuation cycle at the source.

Tests: `tests/test_cli.py::TestCliWorktreeGitPull` — 5 new tests covering:
- Corruption detected → `read-tree` called before `pull`
- Below threshold → no healing
- Exactly at threshold (50) → no healing (threshold is >50)
- Healing triggers both `read-tree` and `update-index --refresh`
- Warning message printed to stderr

## Verification

- [x] `git status --short` shows no D or ?? lines after recovery
- [x] Push completes with hooks enabled (no `SKIP_TESTS=1` bypass)
- [x] Pre-push guard hook catches corruption before it can be pushed (16/16 tests pass)
- [x] Session startup auto-heals corrupt index before autostash captures it (5/5 new tests pass)
- [ ] Confirm corruption does not recur across session boundaries (requires next session observation)

## Lessons Learned

- The worktree index is a separate file from the main checkout index — it's more fragile because
  fewer git operations are designed with worktrees in mind
- **`git stash` perpetuates corruption** — any stash operation (including `--autostash`) on a
  corrupt index faithfully saves and restores the corruption. Fix the index BEFORE stashing.
- **Guards at push time are not enough** — corruption happens at session start (autostash cycle),
  not just at push time. The fix must be at the point where `--autostash` runs.
- Rebase conflict resolution must happen in a foreground, stateful shell context — not chained
  background commands
- The pattern `D` (many) + `??` (same files untracked) is the diagnostic signature: run
  `git read-tree HEAD && git update-index --refresh` immediately
