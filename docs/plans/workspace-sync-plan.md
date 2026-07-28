---
title: "ai ws — Workspace-wide git pull/rebase for all repos and worktrees"
category: plan
tags: [git, worktrees, workspace, sync]
status: in_progress
source: internal
date: 2026-04-25
linked_task: AI-CLI-64
template_version: "plan-1.0.0"
---
<!-- doc:region name="overview" kind="replaceable" -->

# ai ws — Workspace-wide git pull/rebase

**Status:** IN PROGRESS

**Created:** 2026-04-25

**Task:** `AI-CLI-64`

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
- [Design](#design)
  - [Workspace file parsing](#workspace-file-parsing)
  - [Per-repo pull logic](#per-repo-pull-logic)
  - [Output format](#output-format)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Overview

Running `git pull --rebase` across all project repos and their worktrees is tedious in VS Code's Source Control panel — each repo requires a separate click. This command automates it: `ai ws pull` reads a VS Code `.code-workspace` file, enumerates all project folders and their worktrees, and runs `git pull --rebase` on each one.

**Primary use case:** after arriving at the Mac from Hetzner work, pull all repos to local main in one command instead of clicking each source-control sync button.

**Workspace files:**

- `~/projects/sergei/ai-core-local.code-workspace` — default, local repos (13 folders)
- `~/projects/sergei/ai-core-remote.code-workspace` — optional, remote/server repos

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

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
| D1 | How to enumerate repos | (a) Config list, (b) Parse `.code-workspace`, (c) Scan `~/projects/` | `APPROVED: (b)` |
| D2 | Command name | (a) `ai ws pull`, (b) `ai sync repos`, (c) `ai ws sync`, (d) `ai sync workspace` | `APPROVED: (a)` |

<!-- DECISION FORMATTING (AIH-114) — applies when filling in REAL option content below:
  each option's Pros and Cons must be BULLETED lists, and `**Pros:**` / `**Cons:**` must be
  each on its own line — a blank line before each header, and a hard newline between the header
  and its bullet list — otherwise PDF export collapses them onto one line. The placeholder
  skeleton below already shows the correct shape; match it exactly. -->

### D1: How to enumerate repos — `[APPROVED: (b) Parse .code-workspace]`

#### (a) Hardcoded list in config.toml

Enumerate repos from a config key in `config.toml`.

**Pros:**
- Simple, no workspace file parsing

**Cons:**
- Duplicates information already in the `.code-workspace` file
- Two sources of truth diverge over time

#### (b) Parse `.code-workspace` file

Read the VS Code workspace JSON (with trailing comma / comment tolerance via `json5` or manual strip) to get folder paths. Derive each repo root. Enumerate worktrees via `git worktree list --porcelain`.

**Pros:**
- Single source of truth — the workspace file already defines the project fleet
- No config to maintain separately
- Works automatically as repos are added/removed from the workspace

**Cons:**
- VS Code workspace JSON is JSON5 (trailing commas, comments) — standard `json.loads` rejects it; need comment/comma stripping

#### (c) Scan `~/projects/` for git repos

Walk `~/projects/`, find all git repos.

**Pros:**
- Requires no workspace file

**Cons:**
- Too broad — `~/projects/` contains throwaway experiments, template checkouts, etc.
- Workspace file already defines intent

#### Recommendation

> **Decision:** `APPROVED — (b) Parse .code-workspace`

Parse the workspace file — it is already maintained as the authoritative list of active projects. JSON5 handling: strip single-line `//` comments and trailing commas before parsing with `json.loads`. No external dependency needed for this simple case.

---

### D2: Command name — `[APPROVED: (a) ai ws pull]`

#### (a) `ai ws pull`

**Pros:**
- Short and memorable
- `ws` group leaves room for future workspace commands (`ai ws list`, `ai ws open`, `ai ws status`)
- `pull` is the exact git verb — no ambiguity about what operation runs

**Cons:**
- Introduces a new `ws` command group not yet present in the CLI

#### (b) `ai sync repos`

**Pros:**
- Consistent with any existing `ai sync` family commands

**Cons:**
- `repos` is ambiguous — could mean a registry operation, not a git pull
- Doesn't hint at the workspace-file-driven scope

#### (c) `ai ws sync`

**Pros:**
- `ws` group; `sync` is familiar terminology

**Cons:**
- `sync` is vaguer than `pull` — could imply bidirectional or push behavior

#### (d) `ai sync workspace`

**Pros:**
- `workspace` noun makes the VS Code workspace file context explicit
- Fits a `sync` family

**Cons:**
- Verbose; `ai sync vs-code-repos` variant (user-suggested) is even longer
- `workspace` by itself is ambiguous without the VS Code context

#### Recommendation

> **Decision:** `APPROVED — (a) ai ws pull`

`ai ws pull` is the right call. `pull` names the exact git operation; `ws` groups the command correctly and creates a home for future workspace sub-commands. All alternatives are either longer, vaguer, or both.

---

## Design

### Command interface

```text
ai ws pull [--workspace PATH] [--remote] [--dry-run / -d] [--verbose / -v]
```

- `--workspace PATH` — explicit workspace file path (default: `~/projects/sergei/ai-core-local.code-workspace`)
- `--remote` — use `ai-core-remote.code-workspace` instead of the default local one (mutually exclusive with `--workspace`)
- `--dry-run` / `-d` — print what would be pulled, no git operations
- `--verbose` / `-v` — show full git output per repo/worktree

**Short flags:** `-d`/`--dry-run`, `-v`/`--verbose`, `-r`/`--remote`. Both short and long forms required per project CLI conventions.

---

### Workspace file parsing

```python
def _parse_workspace_folders(workspace_path: Path) -> list[Path]:
    """Return absolute paths of all folders in a .code-workspace file."""
    text = workspace_path.read_text()
    # Strip // comments and trailing commas (JSON5 subset)
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r',(\s*[}\]])', r'\1', text)
    data = json.loads(text)
    base = workspace_path.parent
    return [(base / f["path"]).resolve() for f in data.get("folders", [])]
```

---

### Per-repo pull logic

For each workspace folder path:

1. **Exists check** — skip silently if path doesn't exist
2. **Git check** — `git -C <path> rev-parse --git-dir` — skip with warning if not a git repo
3. **Main tree pull:**
   - `git -C <path> status --porcelain` — if clean: `git -C <path> pull --rebase`; log `✓ <name> main`
   - If dirty: `git -C <path> stash push -m "ai-ws auto-stash"`, pull, `git stash pop`; log `⚠ <name> main (stashed)`
4. **Worktree enumeration:** `git -C <path> worktree list --porcelain` — parse `worktree` lines, skip bare and main
5. **Per-worktree pull:**
   - `git -C <wt> status --porcelain` — if clean: `git -C <wt> pull --rebase`; log `✓ <name>/<wt>`
   - If dirty: log `↷ <name>/<wt> (dirty, skipped)` — never touch dirty worktrees

---

### Output format

```text
Workspace: ~/projects/sergei/ai-core-local.code-workspace (13 repos)

✓  sergei          main
✓  aido            main   +  .worktrees/sw-1   .worktrees/sw-2
↷  ai-cli-utils    .worktrees/ai-cli-1  (dirty, skipped)
✓  ai-core         main
…

Done: 11 pulled, 1 stashed+pulled, 1 skipped (dirty)
```

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

### T-01: Workspace file parser

**Size:** S | **Batch:** 1

Implement `_parse_workspace_folders(workspace_path)` in a new `src/ai_cli/workspace.py` module. Handles JSON5 comment/trailing-comma stripping.

**Deliverables:**
- `src/ai_cli/workspace.py` (new)

**Acceptance criteria:**
- [ ] Parses `ai-core-local.code-workspace` correctly — returns 13 absolute paths
- [ ] Handles `//` comments and trailing commas without error
- [ ] Missing workspace file raises `FileNotFoundError` with a clear message

**Dependencies:** None

---

### T-02: Per-repo pull logic

**Size:** M | **Batch:** 1

Implement `ws_pull(workspace_path, dry_run, verbose)` in `workspace.py`. Handles main tree and worktree enumeration + pull. Returns summary stats.

**Deliverables:**
- `src/ai_cli/workspace.py` (updated)

**Acceptance criteria:**
- [ ] Clean main tree pulled with `git pull --rebase`
- [ ] Dirty main tree: stash + pull + pop with warning logged
- [ ] Clean worktrees pulled
- [ ] Dirty worktrees skipped (no stash — not safe in active sessions)
- [ ] Non-existent or non-git folders silently skipped
- [ ] `--dry-run` prints actions, no git operations performed

**Dependencies:** T-01

---

### T-03: `ai ws pull` CLI command

**Size:** S | **Batch:** 2

Wire `ws_pull` into `main.py` dispatch under `ai ws pull`. Add `--workspace`, `--remote`, `--dry-run`, `--verbose` options with both short and long forms.

Default workspace path: configurable in `config.toml` under `[workspace]`, falling back to `~/projects/sergei/ai-core-local.code-workspace`. `--remote` resolves to the `[workspace] remote_path` config key. `--workspace PATH` overrides both.

**Deliverables:**
- `src/ai_cli/main.py` (updated)

**Acceptance criteria:**
- [ ] `ai ws pull` runs against default workspace
- [ ] `ai ws pull -r` / `--remote` switches to remote workspace
- [ ] `ai ws pull --workspace /path/to/file.code-workspace` uses explicit path
- [ ] `ai ws pull -d` / `--dry-run` prints plan without touching git
- [ ] `ai ws --help` shows correct usage

**Dependencies:** T-02

---

### T-04: Tests

**Size:** M | **Batch:** 2

Tests for `_parse_workspace_folders` and `ws_pull`. All must fail if function body replaced with `pass`.

Test cases:
- Parser: valid workspace file → correct paths
- Parser: JSON5 with comments + trailing commas → parses cleanly
- Parser: missing file → FileNotFoundError
- Pull: clean main → git pull called
- Pull: dirty main → stash+pull+pop called in order
- Pull: clean worktree → pulled
- Pull: dirty worktree → skipped, log emitted
- Pull: non-existent folder → silently skipped
- Pull: non-git folder → skipped with warning
- Pull: `--dry-run` → no git subprocess calls
- Pull: `--remote` → resolves to remote workspace path

**Deliverables:**
- `tests/test_workspace.py` (new)

**Dependencies:** T-01, T-02

---

### T-05: Docs update

**Size:** S | **Batch:** 3

Update `docs/tools/ai-cli-usage.md` with `ai ws pull` usage. Same commit as T-03/T-04.

**Deliverables:**
- `docs/tools/ai-cli-usage.md` (updated)

**Dependencies:** T-03

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Core parsing + pull logic | Tests pass |
| 2 | T-03, T-04 | CLI wiring + tests | Tests pass |
| 3 | T-05 | Docs | Human review (plan approval gate) |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

---

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope, command name, default workspace path |
| UAT | After Batch 2 | Run `ai ws pull -d` against real workspace, verify output |

---

## Open Questions

1. What should the command name be: `ai ws pull`, `ai sync repos`, or `ai pull-all`?
2. Should the default workspace path be configurable in `config.toml` under `[workspace]`?
3. Should the remote workspace path also be configurable in `config.toml`?

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. Promoted to D2 — see Decisions section. `ai ws pull` approved.
> 2. Yes — default path configurable in `config.toml` under `[workspace]`.
> 3. Yes — remote path also configurable in `config.toml`.
>
> **AI Response Round 1:**
> - OQ1 resolved as D2, approved: `ai ws pull`. See Decisions section.
> - OQ2/OQ3 resolved: both workspace paths configurable under `[workspace]` in `config.toml`. T-03 already reflects this. All open questions closed.

---

> **Feedback Round 2:**
> - <enter feedback here>

---

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-27 | D1: APPROVED (b) Parse `.code-workspace` | Single source of truth; JSON5 stripping via regex |
| 2026-04-27 | D2: APPROVED (a) `ai ws pull` | Short, `ws` group, exact git verb |
| 2026-04-27 | OQ2/OQ3: both yes | Default and remote workspace paths configurable in `config.toml [workspace]` |

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
