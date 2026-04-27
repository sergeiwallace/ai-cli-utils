---
name: git-review
description: Review all git changes — triage each as ship, gitignore, or discard, then commit, push, and sync all worktrees.
---

# git-review

Review the full git working tree, triage every change, ship what belongs, and sync all branches and worktrees.

**Usage:** `/git-review`

## Workflow

1. **Audit** — run `git status` to enumerate all modified, staged, and untracked files/directories
2. **Triage each item** — for every change, decide:
   - **Ship**: stage, commit, push (code changes, doc updates, config that belongs in the repo)
   - **Gitignore**: add an entry to `.gitignore` (build artifacts, local tooling data, OS files, generated output)
   - **Discard**: `git restore` or `git clean` (accidental edits, scratch files, stale output)
   - **Unclear**: flag for user review before taking any action (see below)
3. **Apply gitignore entries first** — add all `.gitignore` lines before staging anything, so ignored files are never accidentally staged
4. **Stage and commit shippable changes** — one atomic commit per logical group; don't batch unrelated changes
5. **Push** — `git push origin HEAD:main`
6. **Sync local main tree** — `git -C ~/projects/<project> pull --rebase`
7. **Sync other worktrees** — run `git worktree list` to find all active worktrees in the repo, then for each one that is not the current worktree:
   - Check for uncommitted changes (`git -C <worktree-path> status --short`)
   - If **clean**: run `git -C <worktree-path> pull --rebase`. If a merge conflict arises, attempt to resolve it; if not resolvable, abort the rebase (`git -C <worktree-path> rebase --abort`) and flag it (see below)
   - If **has uncommitted changes**: do not touch it — leave it for the CC session owning that worktree to sync when ready; flag it (see below)
   - If **unsure how to proceed**: do not act; flag for user review (see below)
8. **Report** — brief summary of: what was shipped, what was gitignored, what was discarded, which worktrees were synced, and any items that were flagged

## Flagging

When in doubt about any file, directory, conflict, or worktree sync — stop and surface it before acting:

> ⚠️ **Needs your input:** `<path>` — [reason: e.g. "not sure if this should be shipped or gitignored", "merge conflict I can't safely resolve", "worktree has uncommitted changes", "unsure if discarding this is safe"]. How would you like me to handle it?

For worktrees that couldn't be synced, also note:

> The CC session owning `<worktree-path>` (branch `<branch>`) should run `/git-review` or `git pull --rebase` to resolve. Let me know if you want me to handle it directly instead.

**Default: when in doubt, flag. Never guess on destructive or ambiguous actions.**
