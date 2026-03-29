---
name: save-state
description: Save session state to memory and docs before compaction or context loss
---

# save-state

Persist all important session state to memory files and docs before compaction or session end.

**Usage:** `/save-state` or triggered automatically by PreCompact hook

## What to Save

### 1. MEMORY.md — Update Active Work Section

- Current task status (what's in progress, what's done)
- Test count (current passing count)
- Any in-progress implementation work
- Commits made this session (add new ones, don't duplicate)
- Unapproved fixes or pending user decisions

### 2. Memory Files — Create/Update as Needed

- New feedback memories from user corrections this session
- New project memories from decisions or context learned
- Update existing memories if information changed

### 3. Research Docs

- Update `docs/research/` with any completed research
- Add new topics if ad-hoc research was done

### 4. Plans and Docs

- If a plan was being worked on, ensure the plan doc is up to date
- If implementation was in progress, note the current step/status

### 5. Task List

- Check TaskList — note any incomplete tasks in MEMORY.md Active Work section

## Checklist

Run through this before saving:

- [ ] MEMORY.md Active Work section reflects current state
- [ ] MEMORY.md Commits section has all new commits
- [ ] Any new feedback memories created and indexed
- [ ] Research docs updated if runs completed
- [ ] Any in-progress plans have current status noted
- [ ] Git status clean (no uncommitted implementation code)

## Rules

- Read MEMORY.md first to avoid duplicating existing entries
- Don't remove old entries — only add/update
- Keep MEMORY.md under 200 lines (it gets truncated beyond that)
- Commit and push the memory/doc updates
