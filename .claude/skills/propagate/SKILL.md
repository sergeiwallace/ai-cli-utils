---
name: propagate
description: Run /persist for this project AND push changes to project-template + all other projects. Restarts other running sessions.
---

# propagate

Persist a rule, convention, or feedback in THIS project's session config, then propagate it to project-template and all other active projects.

**Usage:** `/propagate <what to persist>` or `/propagate` (will ask what to persist)

## Workflow

### 0. Pre-flight drift check

Before propagating new rules, scan the ecosystem for existing drift. This catches rules that were persisted in sergei but never propagated, or projects that fell behind.

**How to check:**
1. Read `sergei.toml` for the project list
2. For each key CLAUDE.md section in sergei (Platform Design Philosophy, Common Patterns, AI Orchestration, Test Requirements, Development Workflow), grep for the rule's key phrase in each project's CLAUDE.md
3. For procedure docs referenced in sergei's CLAUDE.md, check if they exist in each project's `docs/procedures/`
4. For `.mcp.json` rules (e.g., "don't use Playwright MCP"), check if any project's `.mcp.json` contradicts

**Report format:**
```
Pre-flight drift check:
  NEW rules to propagate: N
  DRIFT fixes found: M
    - event-driven rule: missing from 12 projects
    - browser automation: 3 projects have conflicting Playwright MCP config
    - procedure docs: 2 docs missing from template + all projects
  Total projects to update: X
```

Include drift fixes in the same propagation run — don't defer them. The propagation report (step 6) should distinguish NEW rules from DRIFT fixes so the user can see what was intentional vs remedial.

### 1. Persist in this project first

Run the same logic as `/persist`:
- Identify what to persist (behavioral rule, feedback, convention)
- Update the appropriate target (CLAUDE.md, memory file, procedure doc)
- Commit changes in this project

### 2. Update project-template

For rules that should apply to ALL new projects:
- Update `template/CLAUDE.md.jinja` — add to the appropriate section
- If detailed, add a procedure doc template
- Run template tests: `cd ~/projects/project-template && uv run --extra test pytest tests/ -q`
- Commit in project-template

### 3. Update all other active projects

Read `sergei.toml` for the project list. For each active project:

**CLAUDE.md updates:**
- Find the appropriate section (Terminology, Common Patterns, Test Requirements, etc.)
- Only add **generic** rules — skip project-specific content
- If the rule is already present, skip
- If CLAUDE.md would exceed ~400 lines, move detailed content to a procedure doc and link instead
- Revise/compact existing content as needed — these are living documents, not append-only

**Memory file updates:**
- Write the memory file to `~/.claude/projects/-Users-sergeiwallace-Projects-{name}/memory/`
- Create the `memory/` directory if needed
- Update `MEMORY.md` index
- Check for duplicates by filename — update if exists, create if not

**Procedure doc updates:**
- If the change includes a procedure, write to `docs/procedures/` in each project
- Add link from CLAUDE.md if not already there

**For each project, report:**
```
  project-name:    ✓ CLAUDE.md updated + memory_file.md created
  project-name:    ✓ already exists (skipped)
  project-name:    ✗ error: path not found
```

### 4. Ship ALL changes in every affected project

**Every project that was modified must be committed and pushed.** Do not leave uncommitted session config changes in any project.

For each project that had files modified (including this project and project-template):
1. Use `git -C /path/to/project` for all git commands (avoids `cd &&` compound commands and bare repository attack vectors)
2. `git -C ~/projects/{name} add` the specific changed files (CLAUDE.md, skills, agents, hooks, doc templates, memory files, procedures)
3. `git -C ~/projects/{name} commit` with a descriptive message
4. `git -C ~/projects/{name} push`

This is critical — propagation is not complete until all changes are shipped across the entire ecosystem.

### 5. Signal running sessions to restart (after shipping)

For each running Claude tmux session (check with `tmux list-sessions`):
- Touch `/tmp/cc-exit-{tmux-session-name}` to signal auto-exit
- Write `/tmp/cc-resume-prompt-{tmux-session-name}` with context:
  ```
  Session config was updated by /propagate from {this-session}. Check CLAUDE.md and MEMORY.md for changes. Resume any in-progress work.
  ```
- Skip signaling the CURRENT session (it will auto-exit via step 5)

### 6. Report and auto-exit

Print a summary distinguishing new propagations from drift fixes:
```
Propagated: "description of what was persisted"

NEW rules:
  1. Multi-model routing table — added to 12 projects
  2. Duplicate task check — added to 12 projects

DRIFT fixes:
  1. Event-driven rule — added to 12 projects (was in sergei only)
  2. Browser Automation conflict — fixed in apt-switch, agora, hegemony

REHABILITATION:
  1. apt-switch — added 6 skills, 2 hooks, 5 agents (was severely behind baseline)

Per-project:
  sergei:             ✓ already up to date (source of truth)
  humanware:          ✓ CLAUDE.md updated (3 new rules + 2 drift fixes)
  hegemony:           ✓ CLAUDE.md + .mcp.json fixed (Playwright conflict)
  apt-switch:         ✓ rehabilitated (6 skills + 2 hooks + 5 agents + CLAUDE.md)
  project-template:   ✓ CLAUDE.md.jinja + 2 procedure docs + honest skill
  ...
  Restarted sessions: claude-heg-1 (with resume prompt)
```

Then trigger auto-exit for this session:
```bash
touch "/tmp/cc-exit-${CC_TMUX_SESSION}"
```

## Rules

- Always check before writing — never create duplicates
- Only push **generic** rules to other projects' CLAUDE.md (not project-specific)
- If another project has a better/more detailed version, use that as source
- Memory file names must be consistent across projects (same filename everywhere)
- **Ship everything** — every project that was modified must be committed and pushed before signaling sessions or auto-exiting. Propagation is not complete until all changes are shipped across the entire ecosystem. Do NOT leave uncommitted session config changes in any project.
- Always run template tests before committing template changes
- **Detect and fix conflicts** — if a project's config actively contradicts a rule (e.g., lists a removed MCP server as required), fix the conflict, don't just append the new rule. Remove the contradicting config.
- **Distinguish NEW from DRIFT in reports** — the propagation summary must clearly label which changes are new propagations vs drift fixes. This helps the user understand what happened and why.
- **Propagate procedure docs, not just CLAUDE.md** — if a rule references a procedure doc, the doc must exist in every project. Copy it to `docs/procedures/` in each project and to `project-template/template/docs/procedures/`.
- **Baseline rehabilitation** — if a project is significantly behind the template baseline (missing multiple skills, hooks, or agents), fix it in the same propagation run rather than deferring. Report it as "rehabilitation" in the summary.

## Relationship to Other Skills

- **`/persist`** — same as propagate but for THIS project only
- **`/save-state`** — snapshots session state (memory, docs, git). Different purpose.
