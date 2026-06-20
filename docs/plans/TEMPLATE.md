---
title: "Plan Document Template"
category: plan
tags: [plan]
status: template
template_version: "plan-1.0.0"
---

<!-- aido:region name="overview" kind="replaceable" -->

# [Feature/Epic Name] — Implementation Plan

**Status:** DRAFT

**Created:** YYYY-MM-DD

<!-- Optional fields — add as needed:
**Task:** SW-XXX
**Design:** `docs/designs/...`
-->

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

<!-- AUTHORING GUIDANCE:
  Diagrams: all diagrams must use Mermaid syntax (```mermaid blocks). No ASCII art diagrams.
  Feedback blockquotes: the `> **Feedback Round 1:**` stubs after each section are placeholders
    for the user — include them exactly as shown, blank, not pre-filled; do not write into them
    yourself.
  Options quality: every Decision options table must hold genuinely distinct alternatives with
    honest tradeoffs — not one real option + strawmen. If only one approach is reasonable, say so
    and skip the table. -->

## Table of Contents

<!-- AIDO-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
     with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so
     they navigate in-window (incl. VS Code Remote-SSH). `aido toc check` validates this
     once AIDO-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful
     `###`" — a deterministic OR-rule: include a `###` when it (1) has child `####`,
     (2) its section body ≥ ~8-10 lines, (3) its parent `##` is allowlisted (Decisions /
     Open Questions / appendices), or (4) matches a pattern (`### Decision N`, `### D\d+`);
     `<!-- toc:skip -->` / `<!-- toc:include -->` on a heading override the heuristic. -->

- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Implementation Audit](#implementation-audit)
- [Human Gates](#human-gates)
- [Decisions](#decisions)
  - [Decision Summary](#decision-summary)
  <!-- AIH-52: one `  - [D-N: Name](#d-N)` per decision in the real doc — link the STABLE
       short id `#d-N`, NOT the heading auto-slug. Decision headings carry a mutable
       `— `[PENDING]`` / `✅ Approved` suffix (AIH-41) that CHANGES the auto-slug on approval,
       silently breaking every per-decision ToC link. The `<a id="d-N"></a>` anchor placed
       before each decision heading in Decision Details is the durable jump target (honored by
       GitHub + VS Code preview). e.g.:
         - [D-1: Caching layer choice](#d-1)
         - [D-2: Migration strategy](#d-2) -->
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

<!-- Problem, goal, scope in 2-3 sentences -->

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Task Breakdown

> **AC quality rules** (`docs/procedures/ac-writing-practices.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- aido:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- aido:ac-rules:mirror:end -->

### T-01: [Task Name]

**Size:** S/M/L
**Batch:** 1

<!-- Description -->

**Deliverables:** <!-- D1: structured fields; leave a field empty for trivial tasks -->

- Files created: <!-- path/to/new.py, ... -->
- Files modified: <!-- path/to/existing.py, ... -->
- Tests added: <!-- tests/test_xxx.py::TestY, ... -->

<!-- For replacement/refactor tasks (T-02): list existing behaviors BEFORE writing ACs;
     every inventory item gets a parity AC below. See docs/procedures/ac-writing-practices.md.
**Existing behaviors (inventory):**
- <!-- behavior / code path / edge case from reading the source -->
-->

**Acceptance criteria:** <!-- D2/D3: each independently testable, falsifiable, commit-anchored ("X returns Y for Z", not "X works"); ≥1 failure-path per public function changed; parity AC per inventory item for replacement tasks -->

- [ ] <!-- criterion -->

**Dependencies:** None

## Batch Plan

<!-- D4: every batch carries an explicit, verifiable Exit gate (the bar for "this batch is done"),
     not just "human approval" — e.g. "all T-0x ACs checked + `just check` green". -->

| Batch | Tasks | Focus | Exit gate |
|-------|-------|-------|-----------|
| 1 | T-01 | Foundation | <!-- verifiable: all batch ACs checked + tests/gate green --> |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Implementation Audit

> **Step 14 gate** — complete before updating docs or presenting UAT.
> Verify every T-XX task's acceptance criteria against the actual codebase. Check each item off;
> any unmet AC restarts from implementation (step 5), not from planning.
>
> **For replacement/refactor tasks:** re-read the original implementation from git history
> (`git show HEAD~N:path/to/old.py`) and verify every inventory item is accounted for.
> See `docs/procedures/reasoning-checkpoints.md` §9 and `docs/procedures/ac-writing-practices.md`.

### T-01: [Task Name]

- [ ] <!-- AC 1 -->
- [ ] <!-- AC 2 -->

### T-02: [Task Name]

- [ ] <!-- AC 1 -->

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope and approach |
| UAT | After implementation | Approve for merge |

## Decisions

<!-- Implementation approach decisions with pros/cons. Always include at least 2 genuinely
     distinct options with honest tradeoffs. Use numbered decisions (D-1, D-2, ...) when there are
     multiple choices to track; a single decision for simpler plans.
     Decisions live here at the bottom — stable across review → approval (no post-approval section
     move). Rejected options stay inline in each decision permanently (no stripping to an archive).
     On resolution: set the Summary Status to `**Approved**`, set the detail header suffix to
     `✅ Approved: (x) [name]`, update the Recommendation to `> **Decision:** ✅ Approved — (x) Full
     option name`, and add an Approval Log line. -->

### Decision Summary

<!-- One row per decision. Update Chosen / Rationale / Status when decided. -->

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| D-1 | [Decision name] | (a) Option A, (b) Option B | | | `Pending` |
| D-2 | [Decision name] | (a) Option A, (b) Option B | | | `Pending` |

### Decision Details

<!-- For each decision, expand with options, pros/cons, and recommendation.
     Header suffix: `— \`[PENDING]\`` until approved, then `✅ Approved: (x) [name]`.
     Rejected options stay inline here permanently — do not strip them.
     AIH-52: place `<a id="d-N"></a>` + a blank line immediately BEFORE each `#### D-N:` heading.
     The ToC links to that stable `#d-N` id (NOT the auto-slug) so links survive the status-suffix
     flip and any heading rename. Keep the visible `— \`[PENDING]\`` / `✅ Approved` suffix as-is. -->

<a id="d-1"></a>

#### D-1: [Decision name] — `[PENDING]`

**Context.** <!-- AIDO-136: 1-2 sentences — what forces this decision now, what constraints/
prior choices bound it, and what is at stake. Lets a reviewer judge the options without
reconstructing the background. -->

##### (a) [Option A name]

**Pros:**

- <!-- pro -->

**Cons:**

- <!-- con -->

##### (b) [Option B name]

**Pros:**

- <!-- pro -->

**Cons:**

- <!-- con -->

##### Recommendation

> **Decision:** `PENDING` <!-- Update to `✅ Approved — (x) Full option name` when approved -->

<!-- Which option and why. 2-3 sentences. Reference research docs if applicable. -->

---

<a id="d-2"></a>

#### D-2: [Decision name] — `[PENDING]`

<!-- Repeat the same structure: options with pros/cons, then recommendation. -->

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Open Questions

<!-- Open Questions are DISTINCT from Decisions. A Decision has explicit options
     ("Option A vs Option B with tradeoffs") — if a question has that structure, make it a
     Decision, not an Open Question. An Open Question is a genuine unresolved question that
     CANNOT be reduced to concrete alternatives. It must be a real question — not a note,
     observation, or comment to yourself. Never mirror a decision topic here.
     Resolution format: do NOT replace the question text. Prepend `**[RESOLVED]**` before it
     and add a `- **Resolution:** ...` sub-bullet directly below; keep the original question
     text exactly (permanent record). -->

1. <!-- Question 1 -->
2. <!-- Question 2 -->

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> 2. <!-- Response to question 2 -->
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|

<!-- /aido:region name="overview" -->

<!-- aido:region name="decisions" kind="replaceable" -->

<!-- /aido:region name="decisions" -->

<!-- aido:region name="task_breakdown" kind="replaceable" -->

<!-- /aido:region name="task_breakdown" -->

<!-- aido:region name="feedback_rounds" kind="append_only" -->

<!-- /aido:region name="feedback_rounds" -->

<!-- aido:region name="approval_log" kind="append_only" -->

<!-- /aido:region name="approval_log" -->
