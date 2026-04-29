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

## Table of Contents

- [Overview](#overview)
- [Decisions](#decisions)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
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

## Decisions

<!-- Implementation approach decisions with pros/cons. Always include at least 2 options.
     Use numbered decisions (D1, D2, ...) when there are multiple architectural choices to track.
     Use a single decision for simpler plans. -->

### Decision Summary

<!-- Add one row per decision. Update Status column as decisions are approved. -->

| # | Decision | Options | Status |
|---|----------|---------|--------|
| D1 | [Decision name] | (a) Option A, (b) Option B | `PENDING` |
| D2 | [Decision name] | (a) Option A, (b) Option B | `PENDING` |

### D1: [Decision name] — `[PENDING]`

<!-- Change header suffix to `[APPROVED: (x)]` when decided. -->

#### (a) [Option A name]

**Pros:**

- <!-- pro -->

**Cons:**

- <!-- con -->

#### (b) [Option B name]

**Pros:**

- <!-- pro -->

**Cons:**

- <!-- con -->

#### Recommendation

> **Decision:** `PENDING` <!-- Update to `APPROVED — (x) Full option name` when approved -->

<!-- Which option and why. 2-3 sentences. -->

## Task Breakdown

### T-01: [Task Name]

**Size:** S/M/L
**Batch:** 1

<!-- Description -->

**Deliverables:**

- <!-- files created/modified -->

**Acceptance criteria:**

- [ ] <!-- criterion -->

**Dependencies:** None

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01 | Foundation | Human approval |

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

## Open Questions

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
