---
title: "Skill Audit, Copier Automation, and Session Config Drift Prevention"
category: plan
tags: [session-config, skills, copier, project-template, automation, auto-restart]
status: complete
task: AI-CLI-74
source: ai-cli-utils
---

# Skill Audit, Copier Automation, and Session Config Drift Prevention

**Status:** COMPLETE

**Created:** 2026-04-04

**Related:**
- `~/projects/project-template/` — template source
- `~/projects/CLAUDE.md` — projects-wide session config
- `src/ai_cli/main.py` — ai-cli subcommand implementation
- `~/.claude/hooks/config-reload-check.sh` — UserPromptSubmit hook (config change detection)

## Table of Contents

- [Overview](#overview)
- [Skill Audit Findings](#skill-audit-findings)
- [Implementation Phases](#implementation-phases)
  - [Phase 1: Fix skills in project-template](#phase-1-fix-skills-in-project-template)
  - [Phase 2: ai copier-update subcommand](#phase-2-ai-copier-update-subcommand)
  - [Phase 3: Auto-restart on config change](#phase-3-auto-restart-on-config-change)
  - [Phase 4: Run copier across all projects](#phase-4-run-copier-across-all-projects)
  - [Phase 5: Add drift prevention rule to session config](#phase-5-add-drift-prevention-rule-to-session-config)
- [Acceptance Criteria](#acceptance-criteria)
- [Approval Log](#approval-log)

---

## Overview

Four problems solved together:

1. **Skills drifting from the workflow they invoke.** The `implement` skill in downstream projects showed a 7-step workflow instead of the 16-step CLAUDE.md workflow. Copier hasn't been run in a while.

2. **No automation for running copier across all projects.** Running `copier update --defaults --trust` in 14 projects is manual and gets skipped.

3. **Auto-restart on session config change requires user input.** The `UserPromptSubmit` hook detects changes and blocks prompts, but the user has to submit a prompt to trigger the block, then manually type `/exit`. Sessions should self-restart when idle.

4. **No rule to prevent future drift.** Without an explicit session config rule, skills will drift again after the next workflow update.

---

## Skill Audit Findings

| Skill | Action | Issue |
|-------|--------|-------|
| `implement` | **Copier update** | Template already has 16-step workflow. Downstream projects are stale — copier will fix. |
| `propagate` | **Delete** | No remaining use case. Projects-wide config = one symlinked file. Copier handles template-managed files. `/persist` handles per-project CLAUDE.md. Nothing left for propagate to do. |
| `direct` | **Fix model names** | References `gemini-3.0-flash`/`gemini-3.0-pro` — outdated. Should be `gemini-3-flash-preview`/`gemini-3.1-pro-preview`. |
| `review` | **Fix model name** | `gemini-3-pro-preview` → `gemini-3.1-pro-preview` in fallback chain. |
| `next` | **Fix scope** | Template version calls `get_cross_project_priorities` (83–255KB, scopeless). Default should be local roadmap read (per per-project override in ai-cli-utils). Sergei can override with cross-project query in its own SKILL.md. |
| `persist` | **Fix line limits** | States "CLAUDE.md ~400-line limit" — should be "projects-wide ~250 lines; project-specific ~100 lines". |
| `spec`, `save-state`, `status`, `honest`, `audit-docs`, `handoff`, `spend` | **No change** | All current. |

---

## Implementation Phases

### Phase 1: Fix skills in project-template

**Directory:** `~/projects/project-template/template/.claude/skills/`

1. **Delete `propagate/`** — remove the entire skill directory.
2. **`direct/SKILL.md`** — replace `gemini-3.0-flash` → `gemini-3-flash-preview`, `gemini-3.0-pro` → `gemini-3.1-pro-preview`.
3. **`review/SKILL.md`** — replace `gemini-3-pro-preview` → `gemini-3.1-pro-preview` in both fallback chains.
4. **`next/SKILL.md.jinja`** (rename from `SKILL.md`) — default content is local roadmap read. Add copier variable `use_cross_project_next` (bool, default `false`) to `copier.yaml`. When `true`, render the cross-project SQLite query variant instead. Sergei's `.copier-answers.yml` sets `use_cross_project_next: true` — copier re-renders correctly on every `ai copier-update` run with no manual step required.
5. **`persist/SKILL.md`** — update line limits to: "projects-wide ~250 lines; project-specific ~100 lines (~350 combined)". Line limits are a quality/attention heuristic, not a token budget — keep them tight to preserve instruction recall.

Run `cd ~/projects/project-template && uv run --extra test pytest tests/ -q`. Commit and push.

Also update `~/projects/CLAUDE.md`: remove the `/propagate is deprecated` note and replace with a note about `ai copier-update`.

---

### Phase 2: ai copier-update subcommand

**Files:** `src/ai_cli/main.py`, `src/ai_cli/copier_update.py` (new), `tests/test_copier_update.py` (new)

```bash
ai copier-update [--dry-run] [--project PROJECT]
```text

**Discovery:** scan `Path("~/projects").expanduser().glob("*/.copier-answers.yml")`, parse YAML, filter where `_src_path` contains `project-template`. No hardcoded list — new projects are picked up automatically.

**Per project:**
1. `subprocess.run(["copier", "update", "--defaults", "--trust"], cwd=project_dir)`
2. Scan updated files for `<<<<<<<` conflict markers
3. Report `✓ updated`, `✓ no changes`, or `✗ conflicts` per project

**Flags:**
- `--dry-run` — print discovered projects and what would run, no copier invocation
- `--project PROJECT` — single project by name instead of all

**Guard:** `if os.environ.get("AI_CLI_HOST") != "mac": sys.exit("copier-update: runs on Mac only")`.

**Tests:** mock subprocess, verify discovery, verify conflict detection, verify dry-run, verify single-project filter, verify Mac guard.

---

### Phase 3: Auto-restart on config change

**Goal:** CC sessions self-restart when session config changes and the session has been idle for a configurable period, without requiring user input and without interrupting active work or typed prompts.

**Scope:**
- Projects-wide CLAUDE.md change (`~/projects/CLAUDE.md`) → all active CC sessions restart
- Project-specific CLAUDE.md change (`$(pwd)/CLAUDE.md`) → only sessions for that project restart
- Both already covered by the existing per-session watcher (each watcher monitors both files)

**Two components:**

#### A. Watcher loop change (`main.py` bash template)

Add a config change check inside the watcher's `while true; do ... sleep 1; done` loop. The watcher already polls every second and has access to the tmux session name, lock file, and state dir.

Every 10 seconds (not every second — reduce overhead):
1. Compute current hash of `~/projects/CLAUDE.md` + `$(pwd)/CLAUDE.md`
2. Compare against a baseline hash written at session start (`$_ai_state_dir/config-hash-$tmux_session`)
3. If hash changed: write a `$_ai_state_dir/config-changed-$tmux_session` flag with timestamp

Then, every second (existing poll cadence), check:
```bash
if [[ -f "$config_changed_file" ]]; then
    changed_at=$(cat "$config_changed_file")
    now=$(date +%s)
    idle_secs=$(( now - changed_at ))
    if (( idle_secs >= IDLE_THRESHOLD )); then
        # Check pane content — don't interrupt if user is typing
        last_line=$(tmux capture-pane -t "$tmux_session" -p 2>/dev/null | tail -1)
        # CC idle prompt ends with bare "> " — user typing looks like "> sometext"
        if ! echo "$last_line" | grep -qE '^>\s+\S'; then
            rm -f "$config_changed_file"
            touch "$signal_file"  # triggers existing /exit injection
        fi
    fi
fi
```text

`IDLE_THRESHOLD` defaults to 90 seconds, configurable via `config.toml`:
```toml
[session]
config_reload_idle_secs = 90   # seconds to wait before auto-restarting idle session
```text

#### B. `config-reload-check.sh` (no change needed)

The UserPromptSubmit hook already blocks and tells the user to `/exit` when config changes. This remains as the safety net for when: (a) the watcher's idle check hasn't triggered yet, or (b) the user submits a prompt before the auto-restart fires. No changes required.

The watcher's auto-restart is the primary path; the hook block is the fallback.

**Files changed:**
- `src/ai_cli/main.py` (bash template section)
- `~/.config/ai-cli-utils/config.toml` schema (new `[session]` section with `config_reload_idle_secs`)

---

### Phase 4: Run copier across all projects

After Phases 1–2 are shipped:
1. `ai copier-update` — runs across all 14 projects
2. Review any conflict markers reported
3. Commit and push each project that was modified

**Projects:** all active projects in the platform ecosystem. The main platform project may need a project-specific `next/SKILL.md` override — add this after copier runs.

---

### Phase 5: Add drift prevention rule to session config

**File:** `~/projects/CLAUDE.md`

Add to "Common Patterns → Operational Rules":

> `**Skill maintenance**: When modifying dev workflow steps, test requirements, or any session config procedure that a skill references, also update the corresponding skill in `~/projects/project-template/template/.claude/skills/` and run `ai copier-update` to propagate to all projects.`

Commit and push `~/projects/CLAUDE.md` directly (it's the projects-wide file, no copier needed).

---

## Acceptance Criteria

- [x] `propagate` skill deleted from project-template
- [x] `direct`, `review`, `next`, `persist` skills fixed in project-template
- [x] `ai copier-update` subcommand: auto-discovers projects, `--dry-run`, `--project`, Mac guard
- [x] `ai copier-update` picks up new projects automatically (no hardcoded list)
- [x] Watcher loop detects config hash change and auto-injects `/exit` after idle threshold
- [x] Auto-restart respects idle threshold (default 90s, configurable)
- [x] Auto-restart does not fire when user has text in the prompt box
- [x] Projects-wide config change triggers all sessions; project-specific triggers only that project's sessions
- [x] Copier run across all 14 projects; no conflict markers remain
- [x] Sergei gets `next` SKILL.md with cross-project query (via `use_cross_project_next: true` in `.copier-answers.yml`)
- [x] Drift prevention rule added to `~/projects/CLAUDE.md`
- [x] All tests pass (`ruff check`, `ruff format --check`, `pytest`)

---

## Approval Log

| Date | Round | Decisions |
|------|-------|-----------|
| 2026-04-04 | Round 1 | propagate=delete; next=local roadmap default; copier automation=ai copier-update subcommand; auto-restart on idle added as Phase 3; idle threshold=90s configurable; hook block stays as safety net. |
| 2026-04-04 | Complete | All 5 phases shipped. 14/14 projects updated. Template H1/MD025 fixes added. markdownlint-cli2 ignores added to job-pilot/menos/personal-site for procedure/template dirs. |
