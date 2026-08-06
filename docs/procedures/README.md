# Procedures

Repeatable workflows, operational how-tos, and development processes.

**What belongs here:** Step-by-step processes that are followed more than once — how to run smoke tests, how to create Jira issues, how to propagate templates. These are the *detailed how-to* docs that CLAUDE.md links to but doesn't inline (to keep CLAUDE.md compact).

**What does NOT belong here:**
- **One-time plans** → `docs/plans/`
- **Architecture or design decisions** → `docs/designs/`
- **Research findings** → `docs/research/`
- **Reference material** (formulas, specs, API docs) → `docs/designs/` or `docs/reference/`
- **User-facing guides** → `docs/guide/`

**Relationship to CLAUDE.md:** CLAUDE.md contains concise behavioral rules. When a rule needs detailed steps, checklists, or anti-patterns, the detail goes in a procedure doc here, and CLAUDE.md links to it. See the [4-layer session config model](../../.claude/skills/persist/SKILL.md) for how procedures fit into the broader config system.

## Index

| File | Description |
|------|-------------|
| [uv-disk-usage-troubleshooting.md](uv-disk-usage-troubleshooting.md) | Understanding uv's cache vs. tools directories and safe disk reclamation |
