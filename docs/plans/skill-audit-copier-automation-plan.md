---
title: "Skill Audit, Copier Automation, and Session Config Drift Prevention"
category: plan
tags: [session-config, skills, copier, project-template, automation]
status: draft
source: ai-cli-utils
---

# Skill Audit, Copier Automation, and Session Config Drift Prevention

**Status:** DRAFT — awaiting approval

**Created:** 2026-04-04

**Related:**
- `~/projects/project-template/` — template source
- `~/projects/CLAUDE.md` — global session config
- `src/ai_cli/main.py` — ai-cli subcommand implementation

## Table of Contents

- [Overview](#overview)
- [Skill Audit Findings](#skill-audit-findings)
- [Option A — ai copier-update subcommand](#option-a--ai-copier-update-subcommand)
- [Option B — Standalone script in sergei](#option-b--standalone-script-in-sergei)
- [Recommended Approach](#recommended-approach)
- [Implementation Plan](#implementation-plan)
  - [Phase 1: Skill fixes in project-template](#phase-1-skill-fixes-in-project-template)
  - [Phase 2: Copier automation](#phase-2-copier-automation)
  - [Phase 3: Run copier across all projects](#phase-3-run-copier-across-all-projects)
  - [Phase 4: Session config drift prevention](#phase-4-session-config-drift-prevention)
- [Open Questions](#open-questions)
- [Acceptance Criteria](#acceptance-criteria)
- [Approval Log](#approval-log)

---

## Overview

Three related problems to solve in one plan:

1. **Skills are drifting from the workflow they're supposed to invoke.** The `implement` skill in this project was showing a 7-step workflow instead of the 16-step workflow in CLAUDE.md. Root cause: copier hasn't been run in a while, and project-template skills have been updated but not propagated.

2. **No automation for running copier across all projects.** Running `copier update --defaults --trust` in 14 projects is currently a manual, error-prone task that gets skipped or forgotten.

3. **No session config guidance to prevent future skill drift.** Without an explicit rule, CC sessions will keep drifting skill files from the workflow they're meant to execute.

---

## Skill Audit Findings

Reviewed all 13 skills in `project-template/template/.claude/skills/`. Status of each:

| Skill | Status | Issue |
|-------|--------|-------|
| `implement` | **OK in template, stale in projects** | Template has full 16-step workflow. Downstream projects are behind — copier update will fix. |
| `direct` | **Needs fix** | References `gemini-3.0-flash` and `gemini-3.0-pro` — outdated model names. Should be `gemini-3-flash-preview` and `gemini-3.1-pro-preview`. |
| `review` | **Minor fix** | `gemini-3-pro-preview` → `gemini-3.1-pro-preview` in fallback chain. |
| `propagate` | **Needs clarification** | Describes managing copier-managed files (skills, hooks, agents, procedure docs) as part of its workflow, but CLAUDE.md says copier now handles those. Propagate's scope should be narrowed to CLAUDE.md/GEMINI.md cross-project propagation only. Reference the new copier automation for copier-managed files. |
| `next` | **Needs fix** | Template version uses `get_cross_project_priorities` (scopeless cross-project SQLite query, 83KB–255KB). Per CLAUDE.md: "Never call `get_cross_project_priorities` without scoping." The per-project override in ai-cli-utils reads the local roadmap correctly. The template should default to local roadmap read, not the cross-project query. |
| `persist` | **Minor fix** | States "CLAUDE.md ~400-line soft heuristic" but CLAUDE.md says "projects-wide ~250 lines; project-specific ~100 lines." Needs alignment. |
| `spec` | **OK** | Lightweight planning prompt. Still accurate. |
| `save-state` | **OK** | References CC_TMUX_SESSION and auto-exit mechanism — current. |
| `status` | **OK** | Clean, no drift. |
| `honest` | **OK** | Timeless. |
| `audit-docs` | **OK** | No workflow dependencies. |
| `handoff` | **OK** | References `ai handoff post` CLI — current. |
| `spend` | **OK** | aido-specific, accurate. |

---

## Option A — `ai copier-update` subcommand

Add `ai copier-update` as a subcommand in ai-cli-utils. It:
1. Discovers all `~/projects/*/` dirs with `.copier-answers.yml` referencing `project-template`
2. Runs `copier update --defaults --trust` in each
3. Scans for conflict markers (`<<<<<<<`) in updated files
4. Reports per-project result

**Pros:**
- Available on all machines where ai-cli is installed
- Discoverable via `ai --help`
- Consistent with the ecosystem's "ai is the CLI tool" pattern
- Version-controlled and testable

**Cons:**
- Adds a subcommand to ai-cli that's really a dev/orchestration concern, not an AI session management concern
- Copier updates only make sense on Mac (projects don't live on Hetzner)
- Adds scope to ai-cli-utils

---

## Option B — Standalone script in sergei

Add `~/projects/sergei/scripts/copier-update-all.sh`. It does the same discovery + update + conflict scan.

**Pros:**
- Lives in sergei (the orchestration project) where it belongs conceptually
- Doesn't pollute ai-cli with platform tooling
- Simpler — no Python packaging overhead, just bash

**Cons:**
- Not available via `ai` CLI (less discoverable, requires knowing the path)
- Must be invoked with full path or alias
- Not version-controlled alongside ai-cli — two places to check for tooling

---

## Recommended Approach

**Option A (ai copier-update) for the command surface; it calls a shared script.**

Rationale: The `ai` CLI is the canonical way CC sessions invoke tools across the ecosystem. Making it `ai copier-update` means any CC session can run it without knowing where scripts live. The implementation can be thin — a Python wrapper that calls the discovery logic and execs copier. The Mac-only concern is handled by a guard (`if AI_CLI_HOST != "mac": print error and exit`).

The script logic lives in `src/ai_cli/copier_update.py`, keeping main.py clean.

---

## Implementation Plan

### Phase 1: Skill fixes in project-template

**Files:** `~/projects/project-template/template/.claude/skills/`

1. **`direct/SKILL.md`**: Replace `gemini-3.0-flash` → `gemini-3-flash-preview`, `gemini-3.0-pro` → `gemini-3.1-pro-preview`.

2. **`review/SKILL.md`**: Replace `gemini-3-pro-preview` → `gemini-3.1-pro-preview` in both fallback chains.

3. **`next/SKILL.md`**: Replace cross-project SQLite query with local roadmap read (matching the working per-project version in ai-cli-utils). The global `/next` should default to project-scoped, not cross-project.

4. **`propagate/SKILL.md`**: Narrow scope — remove copier-managed files (skills, hooks, agents, procedure docs) from the manual propagation workflow. Add a note that copier handles those via `ai copier-update`. Keep the CLAUDE.md/GEMINI.md cross-project propagation workflow intact.

5. **`persist/SKILL.md`**: Update CLAUDE.md line limit from "~400 lines" to match CLAUDE.md: "projects-wide ~250 lines; project-specific ~100 lines (~350 combined)."

Commit to project-template, run template tests.

### Phase 2: Copier automation

**Files:** `src/ai_cli/main.py`, `src/ai_cli/copier_update.py` (new), `tests/test_copier_update.py` (new)

**`ai copier-update` subcommand:**

```
ai copier-update [--dry-run] [--project PROJECT]
```

- `--dry-run`: print what would be updated without running copier
- `--project PROJECT`: update a single project instead of all

**Discovery logic (`copier_update.py`):**
1. Scan `~/projects/*/` for `.copier-answers.yml`
2. Parse YAML, check `_src_path` contains `project-template`
3. For each match: `subprocess.run(["copier", "update", "--defaults", "--trust"], cwd=project_dir)`
4. After each update: `grep -r "<<<<<<<" project_dir` to detect conflicts
5. Report: `✓ updated`, `✓ no changes`, or `✗ conflicts found` per project

**Guard:** If `AI_CLI_HOST` is not `mac`, print "copier-update runs on Mac only" and exit.

**Tests:** mock subprocess calls, verify discovery logic, verify conflict detection, verify dry-run output.

### Phase 3: Run copier across all projects

After Phase 1 and 2 are complete:
1. Run `ai copier-update` across all 14 projects
2. Review any conflict markers
3. Commit and push each project that was modified

**14 projects:** acn-automation, agora, ai-cli-utils, aido, apt-switch, artelier, aurion, hegemony, humanware-mobile, humanware, job-pilot, menos, personal-site, sergei.

### Phase 4: Session config drift prevention

**File:** `~/projects/CLAUDE.md` (global)

Add to "Common Patterns → Operational Rules":

```
- **Skill maintenance**: When modifying the dev workflow, session config procedures, or any
  content that skills invoke (e.g., test requirements, automated checks), also update the
  corresponding skill in `~/projects/project-template/template/.claude/skills/` and run
  `ai copier-update` to propagate to all projects.
```

Propagate this rule to all project CLAUDE.md files via `/propagate`.

---

## Open Questions

1. **`propagate` skill — keep or deprecate?** CLAUDE.md says `/propagate is deprecated — use /persist instead`. But the propagate skill still handles the CLAUDE.md cross-project propagation workflow that `/persist` doesn't cover. Should we: (a) keep propagate as-is but narrow its scope to CLAUDE.md/GEMINI.md only, (b) merge it into persist with a `--all-projects` flag, or (c) fully deprecate it since copier now handles the copier-managed files and persist handles per-project CLAUDE.md?

2. **`next` skill — cross-project query or local-only?** The template version queries cross-project SQLite. The project-specific override reads local roadmap. Should the TEMPLATE default be cross-project (for the sergei orchestration context) or local (for all project sessions)? Cross-project makes sense in sergei but is wrong in ai-cli-utils, aido, etc.

3. **copier automation location** — confirmed as `ai copier-update` per recommendation, but flagging in case you prefer the standalone script approach.

---

## Acceptance Criteria

- [ ] All skill issues in project-template are fixed and committed
- [ ] `ai copier-update` subcommand exists with `--dry-run` and `--project` flags
- [ ] `ai copier-update` auto-discovers projects via `.copier-answers.yml` — no hardcoded project list
- [ ] New projects added to `~/projects/` are automatically picked up without script changes
- [ ] Copier update run across all 14 projects; no conflict markers remain
- [ ] Session config rule added to CLAUDE.md and propagated to all projects
- [ ] Tests pass (including new `ai copier-update` tests)

---

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
