---
name: status
description: Structured status update from active work, tests, git, and running tasks
---

# status

Structured status update pulling from active work, tests, and git.

**Usage:** `/status`

## Data Sources to Check (in this order)

1. **Active plans** — find any in-progress plan docs in `docs/plans/`
2. **Tests** — run the project test suite:
   - `pytest -q 2>&1 | tail -3`

3. **Git** — `git log --oneline -5` and `git status -s`
4. **Background tasks** — check TaskList for in-progress items

## Output Template

```
## Status

**Tests:** [count] passing
**Branch:** [current branch]

### Active Work

| Task | Status | Plan Doc |
|------|--------|----------|
| [from plan docs — in-progress items] | [phase/status] | [link] |

### Recent Commits

| Hash | Message |
|------|---------|
| [last 5 commits] | |

### Uncommitted Changes

[git status summary, or "Clean"]

### Next Up

[From plan docs or roadmap — what comes after current work]

### Known Issues

[Any bugs, blockers, or deferred items relevant to active work]
```

## Rules

- Always read plan docs fresh — don't rely on memory
- Include links to all relevant docs
- Keep it concise — one line per item, no narrative
- If no items in a section, write "None"
- Check actual files and git — don't guess
