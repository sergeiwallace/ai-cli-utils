---
title: "Design Document Template"
category: design
tags: [design]
status: template
template_version: "design-1.0.0"
---

<!-- DOC-LINK CONVENTION (so references open in the VS Code Markdown preview):
     link other docs as PLAIN RELATIVE Markdown links  [📄 <repo>/<relpath>](<relative-path>)
     — plain link text, NO backticks (a code-span hides the hyperlink styling, reads as un-clickable code).
     The target is an ordinary relative path from THIS doc's directory, e.g. ../research/x.md ;
     cross-repo (siblings under ~/projects): ../../../aido/docs/research/x.md from a sergei/docs/plans/ doc.
     🚫 NEVER use the vscode://file scheme — the built-in preview reads it as a relative path,
     creates phantom vscode:/file/Users/... folders, and opens a blank stub. Relative links just work.
     Full rule: ai-harness projects-wide-session-config/rules/doc-authoring.md. -->

<!-- aido:region name="overview" kind="replaceable" -->

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

<!-- OUTPUT QUALITY RULES (reminder to CC when authoring architect prompt stubs):
  Include the following sections verbatim in every architect prompt you write.
  The architect reads the prompt text directly — these rules must be in the prompt itself,
  not just referenced by pointer. This comment is the reminder; the stub is the delivery mechanism.

  CRITICAL: The architect prompt must explicitly instruct the Opus sub-agent to read THIS FILE
  (docs/designs/TEMPLATE.md) in full and conform their design doc draft to its structure.
  Use this exact line in the prompt:
    "Read `docs/designs/TEMPLATE.md` for required structure and feedback blockquote format.
     Produce a fully-structured draft conforming to it."

  WORKTREE ISOLATION CHECK (do before every architect run): Verify every file listed in
  `## Files to read` actually exists in the active worktree. Worktrees are git-isolated —
  research docs from other branches/worktrees are invisible to the architect unless pulled in:
    git checkout <source-branch> -- docs/research/<filename>.md
  The architect will NOT error on missing files — it silently skips them and produces a
  weaker design doc. Run the check before presenting the prompt for approval.

  ARCHITECT INSTRUCTION — include verbatim in every architect prompt:
  "If a file in the `Files to read` list is missing from the active worktree, do not skip
   it. Search for the latest version in sibling worktrees at `.claude/worktrees/*/`, copy
   it into the active worktree at the expected path, then read it from there. Only skip a
   file if it is absent from every worktree."

## On design doc quality

Every section that presents options must contain genuinely distinct alternatives
with honest tradeoffs — not one real option and two strawmen. If there is truly
only one reasonable approach, say so and skip the options table.

Open Questions are questions arising from the design that cannot be framed as
explicit options. If something has the structure of "Option A vs Option B with
tradeoffs," it belongs in a Decision section — make it one. If the question
cannot be reduced to concrete alternatives, it is an Open Question. It must also
be a genuine question — not a note, observation, or comment to yourself. If it
reads like a note, either place it elsewhere in the doc or investigate further
until it can be resolved and written as a decision.

(Open-Question and Decision resolution formats are documented inline in their own sections
below — see the `## Open Questions` and `## Design Decisions` section comments. Single source,
co-located with where they're used.)

## On feedback blockquotes

The template includes `> **Feedback Round 1:**` stubs after each major section.
Include them exactly as shown — blank, not pre-filled. They are placeholders for
the user to write into; do not write into them yourself.

## On diagrams

All diagrams must use Mermaid syntax (```mermaid blocks). No ASCII art diagrams.

-->

## Table of Contents

<!-- AIDO-128: the ToC sits ABOVE the Executive Summary (it is self-referential otherwise).
     D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc, with GitHub-style
     anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate in-window
     (incl. VS Code Remote-SSH). `aido toc check` validates this once AIDO-127 lands. If
     all-`###` proves too noisy, fall back to D5 (a) "meaningful `###`" — a deterministic
     OR-rule: include a `###` when it (1) has child `####`, (2) its section body ≥ ~8-10
     lines, (3) its parent `##` is allowlisted (Design Decisions / Open Questions /
     appendices), or (4) matches a pattern (`### D-N`); `<!-- toc:skip -->` /
     `<!-- toc:include -->` on a heading override the heuristic. -->

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
- [Core System / Component](#core-system--component)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
- [Implementation Audit](#implementation-audit)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
  <!-- AIH-52: one `  - [D-N: Name](#d-N)` per decision in the real doc — link the STABLE
       short id `#d-N`, NOT the heading auto-slug. Decision headings carry a mutable
       `— `[PENDING]`` / `✅ Approved` suffix (AIH-41) that CHANGES the auto-slug on approval,
       silently breaking every per-decision ToC link. The `<a id="d-N"></a>` anchor placed
       before each decision heading in Decision Details is the durable jump target (honored by
       GitHub + VS Code preview). e.g.:
         - [D-1: Composition vs inheritance](#d-1)
         - [D-2: Capability advertisement vs routing](#d-2) -->
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Executive Summary

<!-- What does this design build? What does it enable for the end user?
     What problem does it close? What is explicitly deferred / out of scope?
     What are the 1-2 most consequential decisions a non-expert reader should understand?

     4-6 sentences. Readable in 30 seconds cold in a meeting. Write in plain English —
     no jargon a non-specialist would not know, no hedging, no passive voice.
     Do not repeat the Problem Statement verbatim. -->

## Problem Statement

<!-- What problem does this solve? Why now? -->

## Design Overview

<!-- FUNCTIONAL DESCRIPTION OF WHAT WAS BUILT — for external readers, future
     reference, and onboarding. Distinct from Problem Statement (which is "why"
     in 2-3 sentences) and Design Decisions (which is the historical record of
     options considered).

     Starts as a stub during the design/review phase. Fill in fully at dev workflow
     step 15 (doc updates) with concrete implementation knowledge: what was built,
     key architectural choices as established facts, what's in/out of scope, what
     changed from the original design.

     Re-update any time the design doc is revisited for future work or additions.
     Never leave this as a stub after implementation is complete.

     Write in plain English — read like a technical reference for someone who has
     not followed the design conversation. No decision history, no "we considered
     X but chose Y" — that goes in Design Decisions. -->

**Status:** stub — to be filled during/after implementation.

## [Core System / Component]

<!-- Detailed explanation of the design. Use subsections as needed.
     Diagrams: use Mermaid syntax (```mermaid blocks). No ASCII art diagrams. -->

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

<!-- Phased rollout if applicable. D4: give each phase an explicit, verifiable Exit gate
     (the bar for "this phase is done" — e.g. "ACs P1.x checked + gate green"), not a vague
     "human approval". -->

<!-- Per-phase task ACs follow the canonical AC quality rules. `docs/procedures/ac-writing-practices.md`
     is AUTHORITATIVE (open it for the full/latest standard; this inline reminder is sync-checked
     against its canonical block by `aido validate-doc` and must not be edited independently): -->
<!-- aido:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- aido:ac-rules:mirror:end -->

<!-- Example phase shape:
### Phase 1: [Name]
- Scope: <!-- what ships -->
- **Exit gate:** <!-- verifiable bar, e.g. "Phase-1 ACs checked + `just check` green" -->
-->

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
>
> **For replacement/refactor tasks:** re-read the original implementation from git history
> (`git show HEAD~N:path/to/old.py`) and verify every behavior is either preserved or explicitly documented as dropped.
> See `docs/procedures/ac-writing-practices.md`.

| # | Section / Decision | Verified | Notes |
|---|--------------------|---------|-------|
| 1 | **Design Overview filled** — confirm the `## Design Overview` section above is filled in with concrete implementation knowledge, not left as a stub | - [ ] | |
| 2 | <!-- component or decision --> | - [ ] | |
| 3 | <!-- component or decision --> | - [ ] | |

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | | | |
| 2 | | | |

## Design Decisions

<!-- Decisions live at the bottom — stable across review → approval (no post-approval section move).
     Rejected options stay inline in each decision's detail permanently (no stripping to an archive). -->

### Decision Summary

<!-- Update Status to `**Approved**` and Chosen/Rationale when decided. -->

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| D-1 | | (a) Option A, (b) Option B | | | `Pending` |
| D-2 | | (a) Option A, (b) Option B | | | `Pending` |

### Decision Details

<!-- For each decision, expand with options, pros/cons, and recommendation.
     Header suffix: `— \`[PENDING]\`` until approved, then `✅ Approved: (x) [name]`.
     Rejected options stay inline here permanently — do not strip them.
     AIH-52: place `<a id="d-N"></a>` + a blank line immediately BEFORE each `#### D-N:` heading.
     The ToC links to that stable `#d-N` id (NOT the auto-slug) so links survive the status-suffix
     flip and any heading rename. Keep the visible `— \`[PENDING]\`` / `✅ Approved` suffix as-is. -->

<a id="d-1"></a>

#### D-1: [Name] — `[PENDING]`

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

<!-- 2-3 sentences: which option and why. Reference research docs if applicable. -->

---

<a id="d-2"></a>

#### D-2: [Name] — `[PENDING]`

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

<!-- Open Questions are DISTINCT from Decisions: genuine unresolved questions that CANNOT be
     framed as A-vs-B options (if it's "Option A vs B with tradeoffs," make it a Decision). Must be
     a real question — not a note/observation.
     Resolution format: do NOT replace the question text — prepend `**[RESOLVED]**` before it, then
     add a `- **Resolution:** ...` sub-bullet directly below; keep the original text (permanent record).
     Example:
       1. **[RESOLVED]** Should X or Y be used for Z? Full original question text here.
          - **Resolution:** X, because [reason]. [Any follow-up Jira or caveat.]
     Unresolved questions remain plain numbered items, no tag. -->

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

<!-- /aido:region name="overview" -->

<!-- aido:region name="decisions" kind="replaceable" -->

<!-- /aido:region name="decisions" -->

<!-- aido:region name="feedback_rounds" kind="append_only" -->

<!-- /aido:region name="feedback_rounds" -->

<!-- aido:region name="approval_log" kind="append_only" -->

<!-- /aido:region name="approval_log" -->
