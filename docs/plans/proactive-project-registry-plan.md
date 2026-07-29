---
title: Proactive Project Registry Registration — Implementation Plan
category: plans
tags: [registry, config, setup, copier, multi-user]
status: DRAFT
source: claude-sonnet-4-6
---

# Proactive Project Registry Registration — Implementation Plan

**Status:** DRAFT

**Created:** 2026-04-25

**Task:** `[AI-CLI-62]`

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

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

The current project registry flow is reactive: `validate_registry_completeness()` hard-blocks at every `ai c/g` launch and prompts interactively for any unregistered `~/projects/` directory. This creates a gap window between `copier copy` (repo creation) and the first manual `ai c` launch — automation tools (aido, handoffs) that run before the first manual session will hit the hard-block. Additionally, the package should work gracefully for anyone who installs it from PyPI, where the task prefix / roadmap doc system is optional and the hard-block is a bad first-run experience.

The goal is to: (1) add an `ai register` subcommand callable non-interactively so copier `_tasks` hooks can register projects at creation time; (2) make the registry feature opt-in so new pip users are not hard-blocked; (3) optionally support project-local config in `pyproject.toml` (`[tool.ai-cli]`) for self-describing private repos.

**Scope:** `ai-cli-utils` only — copier hook addition is in `project-template` and tracked separately.

**Background from design discussion (2026-04-24/25):**

- `myproject.toml` is non-negotiable as the authoritative registry — it drives session naming (`c-AIH-1`), `-p project` flag, remote session prefix, and `get_project_aliases()` lookup. Any proactive mechanism still writes to the registry; it just replaces the interactive prompt.
- Open-source repos (ai-cli-utils, hegemony, etc.) cannot carry instance-specific project-local config. The optional project-local file is for **private repos** that want self-describing identity — anyone cloning them gets auto-registered without the interactive gate.
- The belt is `myproject.toml`. The suspenders are: `ai register` (scriptable), copier `_tasks` hook (proactive), optional `[tool.ai-cli]` in `pyproject.toml` (self-describing).
- The copier hook closes the gap window — the main pain point. The project-local file is a nice-to-have for the open-source-aware multi-user use case. Both are small additions (~35 LOC total across `main.py` + `config.py`).

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

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
| D1 | Registry feature opt-in gating | (a) Always-on hard-block (current), (b) Skip if `main_project` unconfigured | `PENDING` |
| D2 | `ai register` subcommand scope | (a) Standalone non-interactive CLI, (b) Enhanced `ai setup` wizard, (c) Both | `PENDING` |
| D3 | Project-local config in `pyproject.toml` | (a) Skip (myproject.toml only), (b) Optional `[tool.ai-cli]`, (c) Separate `.ai-project.toml` | `PENDING` |
| D4 | Multi-user first-run UX | (a) Manual `ai setup` after install, (b) First-run auto-detect + prompt, (c) Graceful no-op if unconfigured | `PENDING` |
| D5 | Copier hook mechanism | (a) `_tasks` entry calls `ai register`, (b) Inline Python in `_tasks`, (c) Separate post-copy script | `PENDING` |

<!-- DECISION FORMATTING (AIH-114) — applies when filling in REAL option content below:
  each option's Pros and Cons must be BULLETED lists, and `**Pros:**` / `**Cons:**` must be
  each on its own line — a blank line before each header, and a hard newline between the header
  and its bullet list — otherwise PDF export collapses them onto one line. The placeholder
  skeleton below already shows the correct shape; match it exactly. -->

---

### D1: Registry feature opt-in vs. always-on — `[PENDING]`

**Problem:** `validate_registry_completeness()` is called at every `ai c/g` launch and hard-blocks with an interactive prompt if any `~/projects/` dir is unregistered. For a new user who just did `pip install ai-cli-utils`, this is a hostile first-run experience — they didn't ask for a task-prefix registry system.

#### (a) Always-on (current behavior)

**Pros:**

- Ensures registry is always complete for ai-core users who rely on it

**Cons:**

- Blocks new pip users who haven't configured `[project] main_project` — the feature means nothing to them
- Non-interactive environments (CI, aido automation) will block silently if tty check fails
- Can't be called from copier hooks or scripted contexts

#### (b) Skip registry features if `main_project` unconfigured

**Pros:**

- Graceful for new users — `main_project` is an opt-in ai-core feature
- No behavior change for existing ai-core users (they have `main_project` configured)
- Safe in non-interactive environments

**Cons:**

- Existing ai-core users who haven't set `main_project` lose the validation (minor — they should have it set)

#### Recommendation

> **Decision:** `PENDING` — Recommend **(b)**. The registry is a ai-core-specific feature; basic session management (`ai c N`, `ai ls`, etc.) should work out of the box for any pip user. The guard condition is simple: `if not config.get("project", {}).get("main_project"): return`. Zero behavior change for ai-core users.

---

### D2: `ai register` subcommand scope — `[PENDING]`

**Problem:** `validate_registry_completeness()` is interactive only — it can't be called non-interactively from copier `_tasks` hooks or automation scripts. We need a scriptable path.

#### (a) Standalone `ai register` subcommand

**Pros:**

- Clean single-purpose CLI: `ai register --project ai-harness --prefix AIH --type tool`
- Called from copier `_tasks` hook, fully non-interactive
- Small scope — ~15 LOC in `main.py` + `config.py`

**Cons:**

- Doesn't help first-run UX (new user still needs separate `ai setup`)

#### (b) Enhanced `ai setup` wizard that also registers

**Pros:**

- Single command covers first-run: creates `config.toml`, registers projects
- Better new-user experience

**Cons:**

- Mixes two concerns: config initialization and project registration
- More complex; harder to test; harder to call non-interactively

#### (c) Both: `ai register` (non-interactive) + enhanced `ai setup` (wizard)

**Pros:**

- `ai register` for copier/automation; `ai setup` for first-run humans
- Clean separation of concerns

**Cons:**

- Slightly more surface area; two things to document

#### Recommendation

> **Decision:** `PENDING` — Recommend **(c)**. `ai register` is the mechanical primitive needed for automation; `ai setup` is the human-facing wizard. Both are small. Start with `ai register` (needed for copier hook) and enhance `ai setup` in the same batch.

---

### D3: Optional project-local config in `pyproject.toml` — `[PENDING]`

**Problem:** Open-source repos can't carry instance-specific `task_prefix` config. But private repos would benefit from being self-describing — anyone cloning them gets auto-registered without the interactive gate.

#### (a) Skip — myproject.toml only

**Pros:**

- No additional read path; simpler implementation
- copier hook + `ai register` already solve the gap window without this

**Cons:**

- Cloning a private repo still requires manual `ai register` or the interactive prompt

#### (b) Optional `[tool.ai-cli]` section in `pyproject.toml`

```toml
[tool.ai-cli]
task_prefix = "AIH"
project_type = "tool"
```

**Pros:**

- Standard Python project metadata location (PEP 518 / `[tool.*]` namespace)
- ai-cli reads it on first session launch → auto-writes to myproject.toml; skips interactive prompt
- Open-source repos simply omit the section (no private data exposed)
- project-template can include it as a copier variable, auto-populated at `copier copy` time

**Cons:**

- Additional read path on every session launch (one `pyproject.toml` parse; negligible)
- Must document that open-source repos should omit it

#### (c) Separate `.ai-project.toml` file

**Pros:**

- Keeps non-Python repos supported (no pyproject.toml required)

**Cons:**

- Non-standard; adds `.gitignore` and `.gitattributes` noise; `[tool.*]` namespace exists for this purpose

#### Recommendation

> **Decision:** `PENDING` — Recommend **(b)** for private Python repos (the dominant case in ai-core). Defer `.ai-project.toml` (option c) until a non-Python repo actually needs it. Defer from this scope if D5 (copier hook) is sufficient — the project-local read path is a nice-to-have, not critical path.

---

### D4: Multi-user / public package first-run UX — `[PENDING]`

**Problem:** A developer who `pip install`s ai-cli-utils and runs `ai c 1` for the first time should get a helpful guided experience, not a hard-block or a confusing prompt about `myproject.toml`. The task prefix / roadmap system is optional and shouldn't be in their face immediately.

#### (a) Manual `ai setup` after install (current state, essentially)

**Pros:**

- Zero code change; README documents the step
- Users who want the full feature set run `ai setup`

**Cons:**

- `ai setup` currently exists (`AI-CLI-12`) but is not well-documented as a first-run step
- New users may not know to run it

#### (b) First-run auto-detect + prompt on first `ai c N`

**Pros:**

- Guided experience: "Looks like your first time — run `ai setup` to configure your projects dir"

**Cons:**

- Slightly more magic; first-run state adds complexity

#### (c) Graceful no-op if unconfigured (combined with D1b)

**Pros:**

- Zero friction: `ai c N` just works even without setup; registry features silently opt out
- README documents `ai setup` as the way to unlock full features
- Cleanest for open-source package expectations

**Cons:**

- Users don't know they're missing features until they need them

#### Recommendation

> **Decision:** `PENDING` — Recommend **(c)** as the base behavior (D1b gating handles this), paired with a prominent README section: "First-time setup" → `ai setup`. If the registry is unconfigured, features that depend on it (`ai ls -p project`, session naming) degrade gracefully with a clear message rather than a hard-block.

---

### D5: Copier hook mechanism — `[PENDING]`

**Problem:** How does the copier `_tasks` hook call `ai register` at `copier copy` time to proactively register the new project?

#### (a) `_tasks` entry calls `ai register` CLI

```yaml
_tasks:
  - "ai register --project {{ project_name }} --prefix {{ task_prefix }} --type {{ project_type }} || true"
```

**Pros:**

- Simple; uses the new CLI directly; idempotent (`|| true` handles `ai` not installed)
- Consistent with how `_tasks` hooks are used elsewhere in project-template

**Cons:**

- Requires `ai` CLI to be installed on the machine running `copier copy`; no-op if not

#### (b) Inline Python in `_tasks`

**Pros:**

- No ai-cli-utils dependency at copy time; directly writes TOML

**Cons:**

- Duplicates the registry write logic; fragile if config path changes; harder to maintain

#### (c) Separate post-copy script

**Pros:**

- Explicit; can include error handling and fallback

**Cons:**

- More files to maintain; `_tasks` is already the standard mechanism

#### Recommendation

> **Decision:** `PENDING` — Recommend **(a)**. The `|| true` guard makes it gracefully no-op when `ai` isn't installed. The copier hook is the right place; `ai register` is the right interface. If `ai` isn't installed, the user still gets the interactive prompt at first `ai c` launch — no regression.

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

### T-01: Make registry validation opt-in (D1)

**Size:** S
**Batch:** 1

Guard `validate_registry_completeness()` in `config.py` with a check for `main_project` being configured. If `main_project` is absent from config, return immediately (no prompt, no block). Update the two call sites in `main.py` (lines ~1130, ~1139).

**Deliverables:**

- `src/ai_cli/config.py` — early-return guard in `validate_registry_completeness()`
- `tests/test_config.py` — test: missing `main_project` → no prompt, no block

**Acceptance criteria:**

- [ ] `validate_registry_completeness()` returns immediately if `main_project` not in config
- [ ] Existing ai-core users with `main_project` configured: no behavior change
- [ ] Test: `validate_registry_completeness(interactive=True)` with no `main_project` → returns, no side effects

**Dependencies:** None

---

### T-02: `ai register` subcommand (D2)

**Size:** M
**Batch:** 1

Add `ai register --project NAME --prefix PREFIX --type TYPE [--projects-dir DIR]` subcommand. Non-interactive. Writes `[[projects]]` entry to `myproject.toml` (or configured registry path). Idempotent — updates existing entry if name matches, creates new if not. Prints confirmation or "already registered, updated." Exit 0 on success; exit 1 on registry not found (registry path must exist).

**Deliverables:**

- `src/ai_cli/main.py` — `register` subcommand handler
- `src/ai_cli/config.py` — `register_project(name, prefix, type, projects_dir=None)` function
- `tests/test_config.py` — unit tests for `register_project()`
- `tests/test_main.py` — CLI integration tests via `CliRunner`

**Acceptance criteria:**

- [ ] `ai register --project foo --prefix FOO --type tool` writes entry to registry TOML
- [ ] Idempotent: running twice does not create duplicate entries
- [ ] `--project`, `--prefix` required; `--type` defaults to `"tool"`
- [ ] Exit 1 with clear error if registry path does not exist (user must run `ai setup` first)
- [ ] `ai register --help` shows usage
- [ ] 4+ tests covering: create, update, duplicate-idempotent, missing-registry

**Dependencies:** T-01

---

### T-03: Enhanced `ai setup` — register projects on first run (D2, D4)

**Size:** S
**Batch:** 2

Extend the existing `ai setup` command to: (1) scan `projects_dir` for existing directories not in the registry and offer to register them in batch, (2) display a summary of registered projects after setup. Make the task-prefix prompt optional — allow skipping with a default `TODO` prefix that the user can update later.

**Deliverables:**

- `src/ai_cli/main.py` — enhanced `setup` handler
- `tests/test_main.py` — tests for enhanced setup flow

**Acceptance criteria:**

- [ ] `ai setup` on a fresh install: creates `config.toml` with sensible defaults, scans projects_dir, offers to register found directories
- [ ] Task-prefix prompt is skippable (default `TODO`)
- [ ] Setup completes without error if user skips all optional prompts
- [ ] README "First-time setup" section updated to reference `ai setup`

**Dependencies:** T-01, T-02

---

### T-04: Optional `[tool.ai-cli]` read path in `pyproject.toml` (D3)

**Size:** S
**Batch:** 2

Add a read path in `validate_registry_completeness()` (or a new `_read_project_local_config()` helper): if the current working directory contains a `pyproject.toml` with `[tool.ai-cli]` section, extract `task_prefix` and `project_type`, auto-register without prompting, and write to `myproject.toml`. Fires only if the project is not already registered.

**Deliverables:**

- `src/ai_cli/config.py` — `_read_project_local_config(cwd)` helper + integration into `validate_registry_completeness()`
- `tests/test_config.py` — tests: pyproject.toml present with `[tool.ai-cli]` → auto-register; absent → fall through to interactive prompt

**Acceptance criteria:**

- [ ] `pyproject.toml` with `[tool.ai-cli] task_prefix = "FOO"` → auto-registers without prompt
- [ ] Idempotent: already-registered project → no-op
- [ ] Missing or empty `[tool.ai-cli]` → falls through to normal flow
- [ ] 3+ tests

**Dependencies:** T-01, T-02

---

### T-05: Copier `_tasks` hook in project-template (D5)

**Size:** S
**Batch:** 3

Add `ai register` call to `project-template/_tasks` so every `copier copy` proactively registers the new project. Requires: (a) `task_prefix` copier variable in `copier.yaml`, (b) `_tasks` entry that calls `ai register` with the provided prefix. Graceful no-op if `ai` CLI is not installed.

**Deliverables:**

- `~/projects/project-template/copier.yaml` — `task_prefix` variable
- `~/projects/project-template/_tasks` or equivalent — `ai register` call
- `~/projects/project-template/template/pyproject.toml.jinja` — `[tool.ai-cli]` section with `task_prefix` variable

**Acceptance criteria:**

- [ ] `copier copy ~/projects/project-template new-project` (with `task_prefix` provided) → `ai register` called → project appears in `myproject.toml`
- [ ] `ai` not installed → `_tasks` exits 0, no error
- [ ] New projects from template include `[tool.ai-cli]` in `pyproject.toml`

**Note:** This task lives in `project-template`, not `ai-cli-utils`. Track as separate myproject roadmap task; listed here for context.

**Dependencies:** T-02 shipped to PyPI

---

### T-06: Docs update

**Size:** S
**Batch:** 3

Update README first-time setup section. Add `ai register` to CLI reference. Document `[tool.ai-cli]` in `pyproject.toml` as optional for self-describing private repos (note: omit in open-source repos).

**Deliverables:**

- `README.md` — "First-time setup" section with `ai setup` as the entry point
- `docs/guides/` or equivalent — `ai register` and `[tool.ai-cli]` usage
- `CHANGELOG.md` — entry for this feature

**Acceptance criteria:**

- [ ] README documents the first-run flow
- [ ] `ai register --help` output matches documentation
- [ ] CHANGELOG entry present

**Dependencies:** T-03, T-04

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02 | Opt-in guard + `ai register` CLI | Plan approval |
| 2 | T-03, T-04 | `ai setup` enhancements + pyproject.toml read path | Human review of Batch 1 |
| 3 | T-05 (project-template), T-06 | Copier hook + docs | Human approval before project-template update |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before Batch 1 coding | Approve scope, resolve D1–D5 |
| Batch 1 review | After T-01 + T-02 | Verify opt-in guard + `ai register` behavior |
| Batch 3 approval | Before project-template update | Confirm copier `_tasks` mechanism |
| UAT | After all batches | Verify end-to-end: copier copy → auto-registered in myproject.toml |

## Open Questions

1. **Hard-block removal scope**: Should `validate_registry_completeness()` also be removed as a hard-block for *ai-core* users who have `main_project` configured but have new unregistered directories? Or should the interactive prompt survive for ai-core users (only disabled for non-ai-core users per D1b)? The current UX is useful for ensuring completeness — the gap window is the real problem, not the prompt itself.

2. **`ai register` write target**: Should `ai register` also update `[tool.ai-cli]` in the project's `pyproject.toml` (if it exists), or only write to `myproject.toml`? Bidirectional sync vs. one-way.

3. **Multi-user install scan**: Should `ai setup` offer a batch "scan and register all existing projects" option for new users who have an existing `~/projects/` structure? Useful for onboarding a new machine without running `ai register` per project.

4. **Open-source doc guidance**: Should the README explicitly say "if this repo is public/open-source, omit `[tool.ai-cli]` from `pyproject.toml`"? Or is the opt-in nature of the section sufficient?

5. **`TODO` default prefix**: Is a `TODO` placeholder prefix in `ai setup` acceptable, or should the setup wizard require a real prefix before proceeding? Risk: `TODO` prefix gets committed to myproject.toml and never updated.

6. **project-template scope**: T-05 touches `project-template`, which is a separate copier-managed project. Should it be a separate roadmap task in myproject, or folded into this AI-CLI task with a dependency note? (Recommendation: separate SW-XXX task, linked here.)

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> 2. <!-- Response to question 2 -->
> 3. <!-- Response to question 3 -->
> 4. <!-- Response to question 4 -->
> 5. <!-- Response to question 5 -->
> 6. <!-- Response to question 6 -->
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Approval Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-04-25 | 0 | Plan drafted from design discussion in myproject sw-1 session; D1–D5 + 6 OQs pending user review |
