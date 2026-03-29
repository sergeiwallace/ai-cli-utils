---
name: auto-ship
description: Auto-ship current implementation — compile, test, commit, push, and PR
---

# auto-ship

Auto-ship the current implementation: build, test, commit, push, and create PR.

Use after the user has confirmed changes are ready to ship (UAT approved).

Follow these steps in order:

1. **Run /simplify** — Review changed code for AI slop, scope creep, unnecessary abstractions. Fix any issues found.

2. **Automated checks** — Run all of these. If any fail, stop and report.



   - `pytest`



3. **Commit** — Stage all changed files relevant to the current work. Write a concise commit message. Do NOT include a Co-Authored-By line.

4. **Push** — Push the current branch to origin.

5. **Create PR** (if on a feature branch) — `gh pr create --base main --head <branch> --title "summary" --body "..."`. Skip if already on `main` or PR already exists.

6. **Report** — Confirm what was shipped: commit hash, branch, PR URL, and test results.
