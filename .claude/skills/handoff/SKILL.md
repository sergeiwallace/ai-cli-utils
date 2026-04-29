# handoff

Post a task to the handoff queue for another Claude Code session to pick up.

**Usage:** `/handoff <task description>` or `/handoff` (will ask what to hand off)

## When to Use

- Delegating work to another `ai` session (any idle session can pick it up)
- The work must be in a DIFFERENT project or on non-overlapping files
- You've confirmed no file conflicts with your own in-progress work

## Workflow

### 1. Validate the handoff is safe

Before posting:
- Confirm the target work is in a different project OR different files
- Check `git status` — no uncommitted changes that would conflict
- Verify no shared file dependencies

### 2. Gather handoff details

Ask for or determine:
- **Title**: short description
- **Priority**: P0/P1/P2/P3
- **Project**: which project directory (e.g., acn-automation)
- **Requires human**: does the task need user input?

### 3. Post to the queue

Write the handoff request using the cli command:

```bash
ai handoff post "Title" "P0" "project-name" "$(cat <<'HANDOFF_MSG'
You are working on ~/projects/{project}. Your task:

## Task: {title} ({priority})

{description}

### Context
{background, decisions, constraints, related docs}

### Steps
1. {step 1}
2. {step 2}

### Constraints
- Follow ai-core conventions (frontmatter on all docs, priority tags, ecosystem alignment)
- Commit and push when done

### When Done
- Run: ai handoff complete "$CC_HANDOFF_FILE"
HANDOFF_MSG
)"
```text

### 4. Report

Print confirmation:
```text
Posted handoff to queue: ~/projects/sergei/.handoff-queue/pending/001-title.md
Priority: P0 | Project: project-name | Requires human: yes/no

Any idle AI session (`ai c` or `ai g`) will pick this up on its next restart.
To manually trigger:
- Claude: touch /tmp/cc-exit-claude-sw-{N}
- Gemini: touch /tmp/gg-exit-gemini-sw-{N}
```text

## How Sessions Pick Up Handoffs

The auto-resume loops (both `ai c` and `ai g`) automatically check `~/projects/sergei/.handoff-queue/pending/` on each restart:
1. Finds the highest-priority pending request
2. Atomically claims it (mv to claimed/)
3. Passes the message body as the resume prompt
4. Marks completed when the session ends

## Queue Structure

```text
~/projects/sergei/.handoff-queue/
├── pending/     # Unclaimed — any session can pick up
├── claimed/     # Being worked on
└── completed/   # Done (audit trail)
```text

## Rules

- Never hand off work that modifies the same files you're working on
- Always explain what you're keeping vs handing off (see CLAUDE.md § Cross-session delegation)
- The handoff message must be SELF-CONTAINED — the target session has no context
- Include file paths, not just descriptions
- If the task requires human input, set `requires_human: true` in frontmatter
- Always include "Follow ai-core conventions" in constraints
