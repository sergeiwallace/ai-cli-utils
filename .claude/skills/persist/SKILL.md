---
name: persist
description: Update session config (CLAUDE.md, GEMINI.md, memory, procedures) with permanent rules or conventions
---

# persist

Update this project's session config with rules, conventions, or feedback that should persist across all future sessions.

**Usage:** `/persist` or `/persist <what to remember>`

## What This Updates

Session config = CLAUDE.md + GEMINI.md + MEMORY.md + memory files + procedure docs. Choose the right target:

| What to persist | Where it goes | Example |
|----------------|---------------|---------|
| Behavioral rule or workflow change | CLAUDE.md / GEMINI.md (if short) or procedure doc (if detailed) | "Always use TDD", "Never delete DB without migrating" |
| User feedback or correction | Memory file (feedback type) + MEMORY.md index | "Don't summarize at end of responses" |
| Project decision or context | Memory file (project type) + MEMORY.md index | "ai-core is the platform, sergei is an instance" |
| Repeatable process | Procedure doc in `docs/procedures/` + link from CLAUDE.md/GEMINI.md | Template propagation workflow |

## Steps

1. **Identify what to persist** — if invoked without context, ask: "What do you want to persist?"
2. **Choose the target** — use the table above
3. **Revise existing content if needed:**
   - Persisting is not just appending — you must **intelligently revise, compact, or offload** instructions to keep within the limits.
   - Rewrite sections that have grown verbose or outdated, or merge related entries.
   - **Hub Limits:** projects-wide ~250 lines; project-specific ~100 lines (~350 combined). `GEMINI.md` uses a tighter ~150-line heuristic limit.
   - If adding a rule pushes a file significantly over its limit, you MUST offload detailed content into a new `docs/procedures/` spoke file and leave only a short summary/link in the Hub config.
   - `MEMORY.md` soft limit: ~200 lines (it's an index, keep it concise by nature)
4. **Write the update:**
   - For CLAUDE.md / GEMINI.md: add to the appropriate section, or revise existing content in that section. Keep them synced in their respective roles.
   - For memory files: create/update file with proper frontmatter, update MEMORY.md index (if using Claude; if Gemini, use `save_memory` for global facts)
   - For procedure docs: create/update in `docs/procedures/`, add link from config docs if not already there
5. **Commit and push** — `git add` the changed files, commit with a descriptive message, and `git push`. Session config changes must be shipped immediately.

## 4-Layer Evaluation

Before writing anything, evaluate which layers this practice needs. Most practices need multiple layers — don't stop at the easiest one.

| Layer | Loaded | Purpose | Put it here if... |
|-------|--------|---------|-------------------|
| **Config (CLAUDE/GEMINI.md)** | Every session | Concise behavioral rules (what/when/why) | It affects planning or decision-making |
| **docs/procedures/** | When linked | Detailed how-tos (steps, checklists, anti-patterns) | It has multi-step workflows too long for main config files |
| **Skills** | On invocation | Execution mechanics | It's triggered by a slash command |
| **Memory files** | When relevant | Cross-session context and state | It's background knowledge, not a behavioral rule |

**Key principle:** If a practice affects how sessions *plan or make decisions*, it must go in CLAUDE.md and GEMINI.md — not just memory or skills.

## Rules

- Read the target file before modifying — don't duplicate existing content
- **Revise and compact freely** — these files are living documents, not append-only logs.
- Config docs are for **rules**, not status or transient info
- Always check if a related procedure doc exists or should be created
- Always commit and push after persisting — session config changes must be shipped immediately

## Relationship to Other Skills

- **`/save-state`** — snapshots session state (memory, docs, git). Different purpose.

## Auto-Exit / Refresh

After all persist work is complete:
- If running in Claude: trigger auto-exit so the ai auto-resume loop restarts Claude with fresh config: `touch "/tmp/cc-exit-${CC_TMUX_SESSION:-}"`
- If running in Gemini CLI: suggest the user run `/memory reload` to dynamically refresh the config.
