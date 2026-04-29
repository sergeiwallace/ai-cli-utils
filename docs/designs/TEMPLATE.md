# [System Name] — Design Document

**Status:** DRAFT

**Created:** YYYY-MM-DD

<!-- Optional fields — add as needed:
**Task:** SW-XXX
**Research:** `docs/research/...`
**Plan:** `docs/plans/...`
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

- [Problem Statement](#problem-statement)
- [Design Decisions](#design-decisions)
- [Core System / Component](#core-system--component)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Problem Statement

<!-- What problem does this solve? Why now? -->

## Design Decisions

### Decision Summary

<!-- Update Status to `APPROVED — (x) Option name` when decided. -->

| # | Decision | Options Considered | Status |
|---|----------|-------------------|--------|
| D1 | [Decision name] | (a) Option A, (b) Option B | `PENDING` |
| D2 | [Decision name] | (a) Option A, (b) Option B | `PENDING` |

### Decision Details

<!-- For each decision, expand with options, pros/cons, and recommendation.
     Add `— \`[APPROVED: (x)]\`` or `— \`[PENDING]\`` suffix to each decision header. -->

#### Decision 1: [Name] — `[PENDING]`

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

> **Decision:** `PENDING` <!-- Update to `APPROVED — (x) Full option name` when approved -->

<!-- 2-3 sentences: which option and why. Reference research docs if applicable. -->

---

#### Decision 2: [Name]

<!-- Repeat the same structure: options with pros/cons, then recommendation. -->

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. Decision 1: <approval or feedback>
> 2. Decision 2: <approval or feedback>
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## [Core System / Component]

<!-- Detailed explanation of the design. Use subsections as needed. -->

> **Feedback Round 1:** Does this approach feel right? What's missing?
> - <enter feedback here>

<!-- When user writes feedback above, AI appends the following pattern (do not remove this comment):

> **AI Response Round N:**
> - <AI response here>

---

> **Feedback Round N+1:**
> - <enter feedback here>

-->

## Data Model

<!-- Dataclasses, config schema, JSON structure, or database tables -->

## Integration

<!-- How this connects to other systems. Cross-references to other design docs. -->

## Implementation Phases

<!-- Phased rollout if applicable -->

> **Feedback Round 1:** Does the phasing feel right — too big, too small? Should anything move earlier or later?
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
> Verify every design section and decision against the actual implementation. Check each item off;
> any gap restarts from implementation (step 5), not from planning.

| # | Section / Decision | Verified | Notes |
|---|--------------------|---------|-------|
| 1 | <!-- component or decision --> | - [ ] | |
| 2 | <!-- component or decision --> | - [ ] | |

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | | | |
| 2 | | | |

## Open Questions

1. <!-- Question 1 -->
2. <!-- Question 2 -->
3. <!-- Question 3 -->

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> 2. <!-- Response to question 2 -->
> 3. <!-- Response to question 3 -->
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
