---
name: save-state
description: Save session state to memory and docs before compaction or context loss
---

# save-state

Snapshot session state to memory files, docs, and git before compaction or session end. This preserves *what happened* and *what's next* — for changing *how to behave*, use `/persist` instead.

**Usage:** `/save-state` or triggered automatically by PreCompact hook

## Process

Complete ALL steps before signaling done. Do not ask for confirmation between steps — run autonomously through the full checklist. **Steps are ordered by priority** — if auto-compaction fires mid-process, the most critical state is already saved.

### Step 1: Update Memory (HIGHEST PRIORITY — do this first)

This is the fastest write and most critical for post-compaction continuity.

**MEMORY.md — Active Work Section:**
- Current task status (what's in progress, what's done)
- **What is planned next** (critical — this is what the post-compaction summary needs)
- Key decisions made this session
- Any in-progress implementation work and current step
- Unapproved fixes or pending user decisions
- Test count (current passing count)

**Memory Files — Create/Update as Needed:**
- New feedback memories from user corrections this session
- New project memories from decisions or context learned
- Update existing memories if information changed

### Step 2: Update Docs (save what's in your head to files)

Priority order — most unsaved discussion synthesis first:

1. **Active docs being edited** — design docs, plan docs, architecture docs that have unsaved discussion synthesis from this session
2. **Plan docs** — if implementation was in progress, update current step/status
3. **Roadmap** — mark completed tasks `[x]`, update progress notes on in-progress tasks

Research docs should already be on disk (research-output-to-file rule). If not, write them before anything else.

### Step 3: Commit and Push (clean git state — lowest priority, survives compaction)

Git state is unaffected by compaction. This step is for clean backup, not data preservation.

**Memory files (`~/.claude/projects/.../memory/`) are NOT git-tracked — they live outside the project repo. Do not attempt to `git add` or commit them.**

- `git status` — check for uncommitted project files
- If there are uncommitted changes: commit them with an appropriate message
- Push all commits to origin/main
- Sync main tree: `git -C ~/projects/<project> pull --rebase`
- **If there are changes that should be discarded:** present a summary for user review. Do NOT discard without approval.

### Step 4: Verify

- [ ] MEMORY.md Active Work section reflects current state and next steps
- [ ] Any new feedback/project memories created and indexed in MEMORY.md
- [ ] Active docs updated with session progress
- [ ] Roadmap tasks updated (completed items marked, progress noted)
- [ ] Git status clean (all changes committed and pushed)

## Rules

- Read MEMORY.md first to avoid duplicating existing entries
- **Revise and compact freely** — update, merge, or remove stale entries to keep files focused
- Keep MEMORY.md under ~200 lines
- **Complete ALL steps autonomously** — do not stop to ask for confirmation
- **Include "what's next"** — always note planned next work in MEMORY.md Active Work section
