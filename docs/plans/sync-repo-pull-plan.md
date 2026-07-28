---
title: "sync repo pull — Auto-pull affected project repos after ai sync pull"
category: plan
tags: [sync, git, worktrees, repos, safety]
status: approved
source: sw-2
date: 2026-04-25
linked_task: AI-CLI-63
template_version: "plan-1.0.0"
---
<!-- doc:region name="overview" kind="replaceable" -->

# sync repo pull — Implementation Plan

**Status:** APPROVED

**Created:** 2026-04-25

**Task:** `AI-CLI-63`

<!-- AIDO-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
  with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so
  they navigate in-window (incl. VS Code Remote-SSH). `aido toc check` validates this
  once AIDO-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful
  `###`" — a deterministic OR-rule: include a `###` when it (1) has child `####`,
  (2) its section body ≥ ~8-10 lines, (3) its parent `##` is allowlisted (Decisions /
  Open Questions / appendices), or (4) matches a pattern (`### Decision N`, `### D\d+`);
  `<!-- toc:skip -->` / `<!-- toc:include -->` on a heading override the heuristic. -->

## Table of Contents

- [Overview](#overview)
- [Decisions](#decisions)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

`ai sync pull` syncs CC memory and JSONL files between Mac and Hetzner via a git staging repo. After a successful pull, the affected project repos (code, docs, markdown in `~/projects/<name>/`) are not automatically updated — the user must manually `git pull --rebase` each one. This adds friction and means repos lag behind memory state after a sync.

This feature adds a `sync_repos()` step at the end of `sync_pull` that identifies which project repos are affected (based on which CC project dirs had files updated) and safely pulls each one. Safety is the primary constraint: no git changes in any worktree may be lost or modified in a way that races with an active CC session.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - approved

---

> **AI Response Round 1:**
> - Scope confirmed. Proceeding to implementation.

---

## Decisions

### Decision Summary

<!-- Recommendation-vs-choice tracking (AIH-148): track the AI recommendation and the human
  choice in SEPARATE columns so preference-divergence is queryable, not buried in prose.
  - Recommended (AI): the AI's pick. If the rec was CORRECTED mid-discussion, put the final pick
  here and KEEP the original recommendation + its reasoning in Rationale (or the detail) — never
  silently overwrite it; the correction is signal.
  - Chosen: the human's final pick. Fill when decided.
  - Diverged?: `Yes` if Chosen != Recommended (final), else `No`. On `Yes`, Rationale MUST state
  WHY the human chose differently — that "why" is the highest-value datapoint.
  Full rules: ai-harness docs/procedures/decision-framework.md (Decision Summary tracking). -->

| # | Decision | Options | Status |
|---|----------|---------|--------|
| D1 | Repo scope: which repos to pull | (a) affected only, (b) all repos | `APPROVED: (a)` |
| D2 | Dirty worktree handling | (a) skip + log, (b) stash + pull + pop, (c) commit | `APPROVED: (a)` |
| D3 | CC session state check | (a) skip only, (b) state affects log severity | `APPROVED: (b)` |
| D4 | memories-only mode | (a) skip sync_repos, (b) still run | `APPROVED: (a)` |

<!-- DECISION FORMATTING (AIH-114) — applies when filling in REAL option content below:
  each option's Pros and Cons must be BULLETED lists, and `**Pros:**` / `**Cons:**` must be
  each on its own line — a blank line before each header, and a hard newline between the header
  and its bullet list — otherwise PDF export collapses them onto one line. The placeholder
  skeleton below already shows the correct shape; match it exactly. -->

### D1: Repo scope — `[APPROVED: (a) affected only]`

Which repos to pull after a sync.

#### (a) Affected repos only

Map updated CC project dirs (e.g. `~/.claude/projects/-Users-...-projects-sergei/`) back to repo paths (`~/projects/sergei/`). Pull only repos whose memory or JSONL files changed in this sync.

**Pros:**
- Scoped and fast — only touches repos that actually changed
- Predictable: user knows exactly which repos were updated

**Cons:**
- Won't catch repos where only code changed on the server (via `git push` from a server session without a corresponding `ai sync`)

#### (b) All registered repos

Pull every project in `~/projects/` that is a git repo.

**Pros:**
- Always fully in sync regardless of how changes arrived

**Cons:**
- Slower, more noise, pulls repos unrelated to the current sync operation

#### Recommendation

> **Decision:** `APPROVED — (a) affected only`

Affected-only matches the scope of what `ai sync pull` knows about. Pulling all repos changes the contract of the command. Users who want a full fleet pull can run `git pull` manually or via a future `ai sync repos` standalone command.

---

### D2: Dirty worktree handling — `[APPROVED: (a) skip + log]`

What to do when a worktree has uncommitted changes.

#### (a) Skip + log

Check `git status --porcelain`. If dirty, skip that tree and emit a log line. Same action regardless of CC session state; CC session state only affects log severity.

**Pros:**
- No files ever modified on disk unexpectedly
- No risk of races with an active CC session
- No stash pop conflicts

**Cons:**
- Worktree stays behind until the CC session manually pulls

#### (b) Stash + pull + pop

`git stash push`, `git pull --rebase`, `git stash pop`.

**Pros:**
- Worktree ends up current even with in-flight work

**Cons:**
- Modifies files on disk during the stash window — races with active CC sessions
- Stash pop can fail with conflicts, leaving worktree in a worse state mid-session
- CC can wake up (user types) between stash and pop

#### (c) Commit on behalf of CC session

Auto-commit uncommitted changes before pulling.

**Pros:**
- None — this is wrong at every level

**Cons:**
- Commits partial/broken implementations
- Unknown commit message, unknown intent
- Could push half-written code to remote

#### Recommendation

> **Decision:** `APPROVED — (a) skip + log`

Skip is the only safe answer for dirty trees. Stash modifies files on disk — any active process (CC or otherwise) reading those files during the window sees incorrect content. The worktree will sync when the CC session finishes (auto-pull on `ai c N` launch) or when the user manually runs `git pull --rebase`.

---

### D3: CC session state check — `[APPROVED: (b) state affects log severity]`

Whether to detect CC session state and how to use it.

#### (a) Skip only (ignore CC state)

Git status is the only gate. Don't inspect tmux or process state.

**Pros:**
- Simpler
- No dependency on tmux being available

**Cons:**
- Log messages can't distinguish "no active session, safe to pull manually now" from "CC is mid-execution, really don't touch this"

#### (b) State affects log severity

Map worktree path → tmux session name (`c-<project>-N` from `.worktrees/sw-N`). Check:
1. `tmux has-session -t <name>` — session exists?
2. `ps -o state= -p <claude_pid>` — `S` (sleeping/idle) vs `R` (running/active)?

Use result to vary log message severity and detail only. Same action (skip) for all dirty worktrees.

**Pros:**
- Better diagnostics: "sw-2 dirty, CC idle — pull manually when ready" vs "sw-2 dirty, CC actively executing — do not touch"
- Useful for future: could escalate to system notification when a CC session is blocking a sync

**Cons:**
- Requires tmux to be available (always true in this context)
- Slight overhead to check process state per dirty worktree

#### Recommendation

> **Decision:** `APPROVED — (b) state affects log severity`

Same action (skip), richer signal. Knowing whether CC is actively mid-execution vs idle helps the user decide when to manually pull. Implementation is straightforward: worktree name → session name mapping is deterministic from the path.

---

### D4: memories-only mode — `[APPROVED: (a) skip sync_repos]`

`ai sync pull --memories-only` runs on every SessionStart hook and is performance-sensitive (~2-5s budget).

#### (a) Skip sync_repos in memories-only mode

`sync_repos()` is not called when `--memories-only` is set.

**Pros:**
- No added latency on SessionStart
- Memories-only is explicitly scoped to memory files — repo pull is out of scope

#### (b) Still run sync_repos

Pull repos even in memories-only mode.

**Pros:**
- Session always starts with current repo state

**Cons:**
- Adds git pull overhead to every session start — latency regression
- `ai c N` already runs `git pull --rebase --autostash` at launch, so this is redundant

#### Recommendation

> **Decision:** `APPROVED — (a) skip`

`ai c N` already pulls on launch. Running again inside the memories-only hook is redundant and adds latency. Skip.

---

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes each task from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
  gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
  structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
  review against the ACs), never self-declared "done". -->

### T-01: `sync_repos()` implementation

**Size:** M
**Batch:** 1

Implement `sync_repos(updated_cc_dirs: list[Path], verbose: bool) -> list[str]` in `sync.py`.

Logic:

1. For each updated CC dir, resolve project path: strip `~/.claude/projects/-Users-...-projects-` prefix → `~/projects/<name>/`
2. Skip if project dir doesn't exist or isn't a git repo (`git rev-parse --git-dir`)
3. **Main tree** (`~/projects/<name>/`):
   - `git status --porcelain` → if clean, `git pull --rebase`; log result
   - If dirty: `git stash push -m "ai-sync auto-stash"`, `git pull --rebase`, `git stash pop`; log warning
4. **Each worktree** (`git worktree list --porcelain` → parse `worktree` lines, skip bare/main):
   - `git -C <worktree> status --porcelain` → if clean, `git -C <worktree> pull --rebase`
   - If dirty:
     - Derive session name: worktree basename `sw-N` → `c-<project>-N`
     - `tmux has-session -t <name>` → if no session: log "dirty, no active session"
     - If session: check `ps -o state=` of `claude` process in that pane → `S` or `R` → log severity accordingly
     - Skip (do not touch working tree)
5. Return list of log lines for caller to display

**Deliverables:**

- `sync.py`: `sync_repos()` function
- `sync.py`: call `sync_repos()` at end of `sync_pull()` when not `--memories-only` and `updated_cc_dirs` is non-empty

**Acceptance criteria:**

- [ ] Clean main tree and clean worktrees are pulled after `ai sync pull`
- [ ] Dirty worktrees are skipped; log line emitted
- [ ] Log severity differs: idle CC session vs active CC session (detected via process state)
- [ ] Main tree dirty: stash + pull + pop with warning logged
- [ ] `--memories-only` flag: `sync_repos()` not called
- [ ] Non-existent or non-git project dirs: silently skipped
- [ ] `--dry-run`: `sync_repos()` not called (or called with dry-run flag, no git operations)
- [ ] `--verbose`: per-worktree git output shown

**Dependencies:** None

---

### T-02: Tests

**Size:** M
**Batch:** 1 (alongside T-01)

Tests derive from T-01 ACs. All must fail if the function body is replaced with `pass`.

Test cases:
- Clean main tree → `git pull --rebase` called
- Dirty main tree → stash + pull + pop called in order
- Clean worktree → pulled
- Dirty worktree, no tmux session → skipped, "no active session" in log
- Dirty worktree, tmux session exists, CC idle (`S`) → skipped, "idle" in log
- Dirty worktree, tmux session exists, CC active (`R`) → skipped, "actively executing" in log
- `--memories-only` → `sync_repos` not called
- `--dry-run` → no git operations
- Non-existent project dir → silently skipped
- Non-git dir → silently skipped

**Deliverables:**

- `tests/test_sync.py`: 10+ new tests for `sync_repos` and its integration in `sync_pull`

**Acceptance criteria:**

- [ ] All new tests pass
- [ ] Full suite (263+ tests) still passes
- [ ] No happy-path-only tests — every skip/warn path covered

**Dependencies:** T-01

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Implementation + tests | Plan approved (✅) → ship |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - approved

---

> **AI Response Round 1:**
> - Single batch confirmed. T-01 and T-02 run together.

---

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | ✅ Approved 2026-04-25 |
| UAT | After implementation | Verify repo pull works on real sync |

## Open Questions

*None — all design questions resolved in pre-plan discussion.*

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-04-25 | 1 | Full plan approved. All 4 decisions resolved. Scope, safety matrix, and CC state detection approach confirmed. |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="approval_log" -->
