---
name: copier-propagate
description: Make requested changes to project-template, then propagate to all repos via ai copier-update with full git sync.
---

# copier-propagate

Make a change to `project-template`, then propagate it to every downstream repo via `ai copier-update`. Ships all git changes (local main branches + clean worktrees) so all repos are in sync with the latest copier version.

**Usage:** `/copier-propagate <description of change to make>`

## What this skill does

1. Makes the requested change(s) in `~/projects/project-template/template/`
2. Commits and pushes to project-template
3. Runs `ai copier-update` across all downstream repos
4. For each repo: resolves conflicts, commits all changes, pushes to origin
5. Syncs git worktrees where safe to do so

---

## Step-by-step behavior

### Step 1 — Make the change in project-template

- Identify which file(s) under `template/` need to change (skills, hooks, CLAUDE.md.jinja, GEMINI.md.jinja, procedure docs, etc.)
- Edit the file(s). Copier-managed files live under `~/projects/project-template/template/`.
- Commit with a clear message describing what changed and why.
- Push to origin immediately (`git push` in `~/projects/project-template/`).

### Step 2 — Scan all repos for uncommitted copier state

Before running `ai copier-update`, check every repo for pending uncommitted copier changes (modified `.copier-answers.yml`, untracked skill/hook directories added by a previous update). Commit those first so the working tree is clean. For each affected repo:

```bash
git -C ~/projects/<repo> add .copier-answers.yml .claude/
git -C ~/projects/<repo> commit -m "chore: apply pending copier update"
git -C ~/projects/<repo> push
```

### Step 3 — Run copier-update

```bash
ai copier-update
```

This applies the latest project-template to every downstream repo listed in `~/projects/*/. copier-answers.yml`. For each repo, copier generates a diff and writes it. Handle what follows in Step 4.

### Step 4 — Per-repo: resolve, commit, push

Work through every repo that copier touched. For each:

**4a. Check for conflicts**
```bash
git -C ~/projects/<repo> status
git -C ~/projects/<repo> diff
```

**4b. Resolve merge conflicts** — copier may produce conflict markers in `.copier-answers.yml` or generated files. Resolve by keeping the intended version (typically: accept new copier-answers commit hash, keep local project customizations). Never discard local custom content.

**4c. Check for dirty git worktrees**
```bash
git -C ~/projects/<repo>/.worktrees/ ls 2>/dev/null  # or equivalent
```
- If a worktree has **no uncommitted changes** (clean): sync it now:
  ```bash
  git -C ~/projects/<repo>/.worktrees/<wt>/ pull --rebase
  ```
- If a worktree has **uncommitted changes** (a CC session is actively working in it): skip the worktree sync. Ship the local main branch only. Note it for later: "Worktree <name> skipped — active CC session; sync after session ships its work."

**4d. Commit the copier changes on main**
```bash
git -C ~/projects/<repo> add -A
git -C ~/projects/<repo> commit -m "chore: copier update — <brief description of change>"
GIT_SSH_COMMAND="ssh -o ServerAliveInterval=30" git -C ~/projects/<repo> push
```

**4e. Sync local main tree** (after push):
```bash
git -C ~/projects/<repo> pull --rebase
```

Repeat 4a–4e for every repo.

### Step 5 — Summary

Report:
- How many repos were updated and pushed
- Which worktrees were synced vs skipped (and why)
- Any repos that had conflicts (and how they were resolved)
- Any failures requiring manual follow-up

---

## Rules

- **Ship each repo before moving to the next** — don't batch all commits at the end.
- **Never force-push** — resolve conflicts via rebase or manual resolution.
- **Never discard local customizations** — copier updates should only add/modify template-managed files, not delete project-specific content.
- **SKIP_TESTS=1 only when pre-existing** — only use `SKIP_TESTS=1 git push` if the test gate was already failing before your changes (pre-existing env issue). Document the reason.
- **Use `GIT_SSH_COMMAND="ssh -o ServerAliveInterval=30" git push`** for repos with slow pre-push hooks.
- **Worktrees with active CC sessions**: ship main only, leave worktree for the session to sync when done.
- **ai-core db file**: `ai_core.db` appearing as untracked is expected — it's the local SQLite file. Do not commit it; ensure it's in `.gitignore`.
