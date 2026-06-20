---
title: "Audit Document Template"
category: audit
tags: [audit]
status: template
template_version: "audit-1.0.0"
---

<!-- aido:region name="scope" kind="replaceable" -->

# [AUDIT-NNN] Short Description

**Status:** in-progress | findings-pending-fix | re-audit-needed | passed | closed

**Created:** YYYY-MM-DD

**Auditor:** <!-- Claude (Opus 4.7) / Sergei / etc. List per-round auditor in the Audit Log -->

**Target artifact:** <!-- file path, commit hash, or doc reference being audited -->

<!-- ============================================================
     FEEDBACK RULES (for AI agents):
       1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
       2. When the user writes feedback: commit the doc immediately BEFORE responding.
       3. Each round is a --- bounded section. Never overwrite prior rounds.
       4. After each round, append a line item to the Audit Log: date, round N, what changed.

     MULTI-ROUND AUDIT MODEL (THIS IS THE NORMAL CASE, NOT THE EXCEPTION)

     Real audits almost always go more than one round. The pattern from the
     CLD hypothesis-update gold-standard audit (3 rounds in a single session):

       Round 1: Main audit
                Auditor reads target + scope + reference material → produces Findings
                (IC-N / JA-N / DV-N / F-N / etc.) + Resolution Pass table mapping
                each finding to RESOLVED (auditor-applicable) or TEAM INPUT NEEDED
                (decision needed before fix). Inline fixes applied where unambiguous.

       Round 2: Verification pass (append-only)
                DIFFERENT auditor or same auditor with explicit verification scope.
                For each Round 1 finding: verify the fix is actually in the target
                doc/code. Report PASS / FAIL / PARTIAL with quoted evidence. May
                surface NEW issues (N-N) introduced by Round 1 fixes themselves.
                NO target-doc edits in this round — verification only.

       Round 3+: Resolution / re-verification (as needed)
                Apply Round 2 findings. Re-grep the artifact to confirm fixes
                landed. Repeat verification if any P0/P1/CRITICAL findings.

     Sign-off comes after all rounds report zero outstanding blocking findings.
     A multi-round audit is the norm. A one-round audit is the exception (only
     when the target is trivial or the auditor found nothing).

     This template treats each round as a first-class section. Add a new
     `## Round N — <descriptor>` block per round. Each round has its own
     Findings, Resolution Pass, and Reviewer Prompt appendix.

     AUDIT-KIND PRESETS (delete sections that don't apply)

       (A) Coherence audit — roadmap ↔ code ↔ plan/design docs
           Finding IDs: per-category groupings (Stale open / Stale done / Drift).
           See ai-harness/docs/procedures/roadmap-coherence-audit.md.

       (B) Doc audit — plan/design doc internal consistency + AC coverage
           Finding IDs: IC-N (internal consistency), JA-N (Jira AC), DV-N
           (domain validity), F-N (independent open-scope findings),
           AD-N (audit decisions requiring team input).
           Gold standard: CLD-Workspace/docs/designs/cld-hypothesis-update-audit.md.

       (C) Code audit — codebase-wide pattern sweep
           Finding IDs: F-NN per occurrence with severity tag in header.
           Verification matrix required (5-10 findings spot-reproduced).

       (D) Claim audit — research doc claims vs primary sources
           Finding IDs: numbered per claim. "Specific Claims Verification" and
           "Source Paper Cross-Reference" tables (Aspect | Detail rows).
           Use HIGH / MEDIUM / LOW confidence labels.

       (E) Implementation audit — step-14 dev workflow gate
           Per-T-XX AC checklist with [ ]/[x]. Original-behavior inventory
           check for replacement/refactor tasks (per ac-writing-practices.md).

       (F) Config / decision audit — session config, settings, policies
           "Changes Already Shipped" section with commit hashes.
           Decision Summary table with Approved / Tracked / Closed / Deferred.

     SEVERITY TERMINOLOGY (pick one set per audit, used consistently)

       Generic / code:  P0 / P1 / P2 / P3
       Doc / IC-style:  CRITICAL / MAJOR / MINOR  +  PASS / WARN / FAIL / FAIL—fixed-inline
       Claim audit:     HIGH / MEDIUM / LOW confidence
       Coherence:       Stale open / Stale done / Drift / Correct / Deferred
       Decisions:       Approved / Tracked / Closed / Deferred
     ============================================================ -->

## Table of Contents

<!-- AIDO-128 / D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc,
     with GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so they
     navigate in-window (incl. VS Code Remote-SSH). `aido toc check` validates this once
     AIDO-127 lands. If all-`###` proves too noisy, fall back to D5 (a) "meaningful `###`"
     — a deterministic OR-rule: include a `###` when it (1) has child `####`, (2) its body
     ≥ ~8-10 lines, (3) its parent `##` is allowlisted, or (4) matches a pattern; manual
     `<!-- toc:skip -->` / `<!-- toc:include -->` override. -->

- [What Was Audited](#what-was-audited)
- [Scope](#scope)
  - [In scope](#in-scope)
  - [Out of scope](#out-of-scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
  - [R1 Summary](#r1-summary)
  - [R1 Findings](#r1-findings)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
- [Round 2 — Verification Pass](#round-2--verification-pass-append-only)
  - [R2 Summary](#r2-summary)
  - [R2 Recommendations](#r2-recommendations)
- [Round 3 — Resolution Pass](#round-3--resolution-pass)
  - [R3 Summary](#r3-summary)
  <!-- Add `Round N — <descriptor>` (+ its `###` sub-entries) here as needed. -->
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
  <!-- AIH-52: if you add per-decision ToC links here, use `  - [AD-N: Name](#ad-N)` — link the
       STABLE short id `#ad-N`, NOT the heading auto-slug. AD-N headings carry a mutable
       `— `[PENDING | APPROVED — (x) | CLOSED]`` suffix that CHANGES the auto-slug on resolution.
       The `<a id="ad-N"></a>` anchor before each AD-N heading is the durable jump target. -->
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

<!-- Audit findings + verification assertions follow the canonical AC quality rules.
     `docs/procedures/ac-writing-practices.md` is AUTHORITATIVE (open it for the full/latest
     standard; this inline reminder is sync-checked against its canonical block by
     `aido validate-doc` and must not be edited independently): -->
<!-- aido:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- At least one failure-path AC per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- aido:ac-rules:mirror:end -->

## What Was Audited

<!-- 2-3 sentences naming the artifact, the commit/version, and why this audit was triggered.
     Examples:
     - "RSMCLD-149 plan doc at commit abc1234, triggered by completion of T-04 implementation"
     - "aido src/aido/nodes/research.py + related synthesis templates at commit 2cd236c,
       triggered by AIDO-107 codebase-wide audit for silent-degradation patterns"
     - "Every open roadmap task in aido/docs/roadmap/master-roadmap.md verified against
       source code and linked plan/design docs (AIDO-91 deliverable)"
     - "cld-hypothesis-update-design.md approved 2026-05-26 (commit ...) — full Opus
       reviewer pass on internal consistency, V7 spec / Jira AC compliance, and domain
       validity before CLD-48 T-4 implementation starts"
-->

## Scope

### In scope

- <!-- file / directory / behavior / AC / task list / claim set being audited -->

### Out of scope

- <!-- what we explicitly did NOT check, and why -->

<!-- Be specific about non-goals. Audit scope creep drags audits. Pinning scope here makes
     re-audits reproducible and lets reviewers verify the audit covered what it claims. -->

## Methodology

<!-- High-level approach across all rounds. Specifics for each round go in its own section.

     For a coherence audit, explicitly state whether each linked plan doc's Approval Log
     was read in full, not just the frontmatter (the AIDO-63 miss pattern).

     For a doc audit, list the validation dimensions (IC / JA / DV / etc.) and the source
     of truth for each (the design doc body / V7 spec / Jira ACs / research papers / etc.). -->

**Approach:**

<!-- 1-3 sentences describing the multi-round shape (e.g. "Round 1 Opus full validation
     pass across 3 dimensions; Round 2 Opus verification pass append-only; Round 3 Sergei
     applies Round 2 findings + re-grep verification"). -->

## Status Summary

<!-- Cross-round status. Update at the end of each round. -->

**Latest round:** Round N

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| CRITICAL / P0 | 0 | 0 | 0 |
| MAJOR / P1    | 0 | 0 | 0 |
| MINOR / P2    | 0 | 0 | 0 |
| Cosmetic / P3 | 0 | 0 | 0 |
| **Total**     | **0** | **0** | **0** |

**Ship-readiness verdict:**

<!-- One paragraph. Examples:
     - "Ready to ship. 0 outstanding findings across 3 rounds. JA-2 PARTIAL upgraded to
       PASS via N-1 fix. CLD-48 T-4 implementation unblocked."
     - "Not ready. 1 CRITICAL (F-1 hook precedent file) requires AD-1 team decision before
       fix can be applied. 2 MAJOR pending Round 3."
     - "Plan is in good shape. Two P2 findings (rollback section, ambiguous AC on T-03)
       and one P3 (header typo). No P0/P1 issues — safe to proceed with implementation."
-->

## Round 1 — Main Audit

<!-- The primary audit pass: read target + scope + reference material, produce findings,
     apply unambiguous fixes inline, surface team-input decisions to AD-N. -->

**Round 1 auditor:** <!-- Claude Opus 4.7 / Sergei / etc -->

**Round 1 date:** YYYY-MM-DD

**Round 1 scope:** <!-- 1-2 sentences. Differs from overall scope only if this round
     deliberately narrowed (e.g. "Round 1: validate 12 design decisions + 33 V7 user
     stories. Independent-findings sweep deferred to Round 2 if time permits"). -->

### R1 Summary

<!-- Brief counts and outcome. Examples:

     - "30 findings: 3 CRITICAL, 15 MAJOR, 12 MINOR. 26 RESOLVED inline or via the
       Resolution Pass table; 4 require team input (AD-1..AD-4). IC-7 + IC-10 fixed
       inline by the reviewer; Executive Summary inserted; Jira hyperlinks normalized."

     - "11 IC checks (10 PASS, 1 FAIL — fixed inline); 4 JA checks all PASS; 6 DV
       checks all PASS; 15 independent F-N findings (3 CRITICAL, 9 MAJOR, 3 MINOR)."
-->

### R1 Findings

<!-- One subsection per finding (or one table for batch findings like IC-N). Number them
     with a prefix that fits the audit kind:

       IC-N   internal consistency check
       JA-N   Jira / spec AC compliance
       DV-N   domain validity check
       F-N    independent finding (open-scope discovery — not on any checklist)
       AD-N   audit decision requiring team input (moved to "Decisions Requiring Team Input")
       N-N    NEW finding from a verification round (introduced by an earlier round's fix)

     EVERY finding must be falsifiable — verification command or file:line reference.
     This is the #1 audit quality bar.

     TABLE FORM (for many small checks like IC-N): -->

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | PASS | <!-- file:line reference + quoted text proving the check --> |
| IC-2 | WARN | <!-- specific evidence quoting the doc --> |
| IC-3 | FAIL — fixed inline | <!-- what was wrong + the fix applied, commit hash --> |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| JA-1 | PASS | <!-- evidence --> |
| JA-2 | PARTIAL | <!-- what's covered, what isn't, link to follow-up --> |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|----|---------|----------|
| DV-1 | PASS | <!-- domain-expert check evidence --> |

<!-- DETAILED FORM (for findings that need full evidence/recommendation): -->

#### F-01: [Short finding title] — `CRITICAL` / `P0`

**Location:** `path/to/file.py:line` (or `docs/plans/foo.md § Section`)

**Evidence:**

<!-- Exact code excerpt, doc text, or output. Quote verbatim — paraphrasing here
     is a failure mode. -->

```python
# example.py:42
def foo():
    pass  # <-- silently returns None instead of raising
```

**Why it matters:**

<!-- 1-2 sentences on actual impact. Tie to a user-visible failure or architectural risk. -->

**Verification command:**

```bash
# Should exit non-zero or produce output proving the bug.
grep -n "pass  # silently" src/example.py
```

**Recommendation:**

<!-- What to change. Specific enough that the fix is unambiguous. -->

#### F-02: [Short finding title] — `MAJOR` / `P1`

<!-- Repeat for each independent finding -->

### R1 Resolution Pass

<!-- One row per Round 1 finding. Status: RESOLVED (fix applied in this round, name how)
     or TEAM INPUT NEEDED (auditor cannot decide alone, decision moved to AD-N). -->

| Finding | Status | How resolved |
|---------|--------|--------------|
| IC-1 | RESOLVED | <!-- e.g. "Added decision_rationale field to events.ndjson schema" --> |
| IC-7 | RESOLVED | <!-- e.g. "Fixed inline by Opus reviewer (3 instances)" --> |
| F-1  | TEAM INPUT NEEDED | <!-- e.g. "§Hooks updated to remove false 'well-trodden' claim; approach (a vs b) — see AD-1" --> |
| F-2  | RESOLVED | <!-- e.g. "Approval Log note rewritten: one new kwarg + cross-skill convention update" --> |

### R1 Verification Matrix

<!-- Spot-check 5-10 findings: re-run each verification command and record the actual
     output. A finding without a reproduced verification command is a hypothesis, not
     a fact. This is the auditor agent's core deliverable (see .claude/agents/auditor.md). -->

| Finding | Command | Expected | Actual | Pass? |
|---------|---------|----------|--------|-------|
| F-01    | `<cmd>` | <!-- expected --> | <!-- actual --> | ✅ / ❌ |
| F-02    | `<cmd>` | | | |

**Verified: X/Y findings reproduce on commit `<hash>`.**

## Round 2 — Verification Pass (append-only)

<!-- Verification round. DIFFERENT scope from Round 1: confirm that Round 1 findings were
     actually applied to the target, surface NEW issues introduced by the Round 1 fixes.
     NO target-doc/code edits in this round. -->

**Round 2 auditor:** <!-- ideally a different agent / model / human than Round 1 -->

**Round 2 date:** YYYY-MM-DD

**Round 2 scope:** Verify every Round 1 finding (IC-N / JA-N / DV-N / F-N) and AD-N
decision is correctly applied to the target. Surface NEW issues (N-N) introduced by
Round 1 fixes. **Append-only — no target edits.**

### R2 Summary

<!-- Examples:
     - "56 PASS, 1 PARTIAL (JA-2), 5 new findings (0 CRITICAL, 1 MAJOR, 4 MINOR)."
     - "All 12 IC checks PASS. AD-1..AD-4 verified applied. N-1 (MAJOR) flags a missing
       PR-description note that the Round 1 Resolution Pass claimed but didn't apply."
-->

### R2.1 Round 1 IC/JA/DV verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | PASS | `decision_rationale` present in events.ndjson hypothesis_weight_update payload at line 669 of design doc |
| IC-2 | PASS | <!-- ... --> |

### R2.2 Round 1 F-N verification

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | PASS | <!-- ... --> |
| F-2 | PASS | <!-- ... --> |

### R2.3 AD-N decisions verification

<!-- For each AD-N: verify the chosen option's implementation is reflected in target. -->

| ID | Verdict | Evidence |
|----|---------|----------|
| AD-1 | PASS | <!-- specific file:line citing the implementation of the chosen option --> |

### R2.4 NEW issues surfaced

<!-- Findings introduced by Round 1 fixes themselves, or gaps Round 1 missed. -->

#### N-01: [Title] — `MAJOR`

**Location:** <!-- file:line -->

**What the Round 1 Resolution Pass claimed:** <!-- quote the resolution row -->

**Actual state:** <!-- what's actually in the target -->

**Why it matters:** <!-- impact -->

**Recommended fix (Round 3):** <!-- specific action -->

#### N-02: [Title] — `MINOR`

<!-- Repeat for each new finding -->

### R2 Recommendations

**MUST be fixed before [downstream event]:** <!-- e.g. "before CLD-48 implementation starts" -->

- <!-- list of blocking items, or "none" -->

**SHOULD be fixed before [next gate]:** <!-- e.g. "before the design-doc PR merges" -->

- <!-- list -->

**Can be folded into a follow-up:**

- <!-- list -->

## Round 3 — Resolution Pass

<!-- Apply Round 2 findings. Re-grep the artifact to confirm fixes landed (built-in
     regression check). Append a "Status after Round 3" with ship-readiness verdict. -->

**Round 3 author:** <!-- Sergei (CC session) / Claude / etc -->

**Round 3 date:** YYYY-MM-DD

**Round 3 scope:** Apply all N Round 2 findings to the target. Re-grep to verify
fixes landed.

### R3 Summary

| Finding | Severity | Resolution |
|---------|----------|------------|
| N-1 | MAJOR | RESOLVED. <!-- specific change applied --> |
| N-2 | MINOR | RESOLVED. <!-- specific change applied --> |
| N-3 | MINOR | DEFERRED to implementation PR. <!-- rationale --> |

### R3 Re-grep Verification

<!-- Built-in regression check. Re-grep the target after fixes to confirm:
     - Stale strings removed
     - New required strings present
     - Section structure as expected -->

Re-grep of the target confirms:

- <!-- e.g. "'D13' no longer appears anywhere in the body (was only on the OQ9 line)." -->
- <!-- e.g. "'Wait —' no longer appears on the Phase 1 cld-hypotheses-summary bullet." -->
- <!-- e.g. "The PR-Description-Notes section now contains 4 bullets (was 2)." -->

### Status after Round 3

<!-- Clear ship-readiness verdict. Examples:
     - "0 CRITICAL, 0 MAJOR, 0 MINOR outstanding from Round 1, Round 2, or analysis-doc
       items. JA-2 verdict upgraded from PARTIAL to PASS (closed via N-1 fix). Design
       doc is ship-ready. CLD-48 T-4 implementation is unblocked."

     - "2 MINOR outstanding (N-3 deferred to impl PR; N-4 deferred to follow-up task).
       No blocking findings. Ready for sign-off."
-->

<!-- ====== Add `## Round N` blocks as needed. Each round repeats the pattern:
     Summary → Findings → Resolution Pass → (optional Re-grep Verification) →
     Status after Round N ====== -->

## Decisions Requiring Team Input

<!-- AD-N items pulled out of Round 1+ findings that require a human/team choice before
     the fix can be applied. Each is a mini-decision-doc: options with pros/cons, a
     recommendation, an approval status. Modeled on docs/designs/TEMPLATE.md decisions.

     Once approved, the chosen option is implemented (often in the next round) and the
     AD-N moves to APPROVED with implementation pointer.
     AIH-52: place `<a id="ad-N"></a>` + a blank line immediately BEFORE each `### AD-N:` heading,
     and link any per-decision ToC entry to that stable `#ad-N` id (NOT the auto-slug) so the link
     survives the status-suffix flip. Keep the visible `— `[PENDING | APPROVED …]`` suffix as-is. -->

<a id="ad-1"></a>

### AD-1: [Decision name] — `[PENDING | APPROVED — (x) | CLOSED]`

**Context:** <!-- 1-2 sentences naming the finding that surfaced this decision -->

#### (a) [Option A]

**Pros:**
- <!-- pro -->

**Cons:**
- <!-- con -->

#### (b) [Option B]

**Pros:**
- <!-- pro -->

**Cons:**
- <!-- con -->

#### Recommendation

> **Decision:** `PENDING` <!-- Update to `APPROVED — (x) Full option name + implementation pointer` -->

<!-- 2-3 sentences on which option and why. After approval, add a one-line pointer
     to where the implementation lives (commit hash / file:line). -->

<a id="ad-2"></a>

### AD-2: [Next decision] — `[PENDING]`

<!-- Repeat for each team-input decision -->

## Outstanding Issues to Fix

<!-- Actionable items derived from Findings that were NOT applied during any audit round
     (deferred to a follow-up plan/PR). Each Issue links back to one or more Findings,
     priority + owner. This is what gets pasted into the roadmap / CC task panel. -->

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-01 | P1 | <!-- description --> | F-01 | <!-- agent/person --> | <!-- commit / PR / date --> |
| I-02 | P2 | <!-- description --> | N-02, N-03 | | |

## Already-Correct Items

<!-- One-line confirmation per AC / behavior / claim / task checked AND passed.
     Load-bearing for credibility: the audit's "passed" claim is only as credible
     as the explicit list of things actually checked.

     A finding-free section here is a red flag — usually means the audit didn't
     actually check anything. Conversely, a long list with specific evidence per row
     is the strongest possible signal that the audit was thorough.

     Examples:
       - "✅ AC T-01 #2 (`pytest tests/foo.py::test_bar`) — passes locally on commit abc1234"
       - "✅ AIDO-89 (machine_profile.py exists, detect_profile() + AI_HOST handling confirmed)"
       - "✅ IC-3 — iteration state machine derives from verdicts.json + computer-report.json
         (per Data Model § lines 865-870)" -->

- ✅ <!-- AC or behavior verified, with reference -->
- ✅ <!-- another verified item -->

## Anti-Patterns to Watch For

<!-- Methodology failure modes from prior audit misses. Copy/extend from
     ai-harness/docs/procedures/roadmap-coherence-audit.md and other procedure docs.
     If this audit found new methodology gaps, ADD them here AND propose updates to
     the relevant procedure doc.

     Common examples:
       - Code-only audit: Checking only whether code exists without reading plan doc
         Approval Logs. Misses "shipped but roadmap not updated" tasks. (AIDO-63 miss.)
       - Frontmatter-only check: Reading only `status:` without checking Approval Log.
       - Skipping linked docs: Trusting the parent-doc description alone without
         following plan/design links. The doc is the source of truth.
       - Partial read: Reading only the first half of a long doc. Approval Logs and
         sign-off sections are at the bottom.
       - One-round audit: Treating Round 1 as final. Verification (Round 2) catches
         the "Resolution Pass claimed X applied, but actually X is missing" failure
         mode (the JA-2 PARTIAL pattern).
       - Verification round that edits the target: Round 2 must be append-only. If
         the verifier edits the target, the next round can't tell what was actually
         applied vs what the verifier patched.
       - No verification matrix: Findings with bash verification commands that nobody
         ran. A finding the auditor didn't reproduce is a hypothesis, not a fact.
       - Inline fixes without commit: Edits applied during audit but not recorded in
         Resolution Pass with a commit hash → impossible to verify or roll back.
       - Empty Already-Correct Items: an audit with 0 findings AND 0 confirmed items
         didn't actually check anything. -->

## Sign-Off Checklist

<!-- What must be true for this audit to close. Trim/extend for the audit kind. -->

- [ ] All CRITICAL / P0 findings have linked fixes (commit hash or PR)
- [ ] All MAJOR / P1 findings fixed OR explicitly deferred with rationale in Outstanding Issues
- [ ] All MINOR / P2 / P3 findings logged to roadmap (even if deferred)
- [ ] All AD-N decisions are APPROVED or explicitly CLOSED with rationale
- [ ] Verification Matrix run on at least 5-10 findings; X/Y reproduce recorded
- [ ] At least one verification round (Round 2+) completed if Round 1 had any findings
- [ ] Re-grep verification done in the final resolution round
- [ ] Inline fixes (FAIL — fixed inline) all have commit hashes in their Resolution Pass row
- [ ] Already-Correct Items populated with specific evidence per row (no empty assertions)
- [ ] Anti-Patterns section reflects this audit's methodology lessons (especially if any
      Round N missed something a Round N+1 caught — that's a procedure update)
- [ ] User reviewed and approved sign-off (Round N)

## Audit Log

<!-- Append-only chronological log. One row per round / event. Never edit prior rows. -->

| Date | Action | Notes |
|------|--------|-------|
| YYYY-MM-DD | Round 1 audit pass complete | <!-- e.g. "Opus reviewer; 3 CRITICAL, 15 MAJOR, 12 MINOR findings; IC-7 + IC-10 fixed inline; Executive Summary inserted" --> |
| YYYY-MM-DD | N findings applied to target | <!-- e.g. "All no-team-input findings resolved in design doc (commit 1514e8b6); AD-1..AD-4 drafted in audit doc (commit 310ca41c)" --> |
| YYYY-MM-DD | AD-1 APPROVED — (a) [option name] | <!-- implementation pointer --> |
| YYYY-MM-DD | Round 2 verification pass complete | <!-- e.g. "Opus reviewer; 56 PASS, 1 PARTIAL (JA-2), 5 new findings (0 CRITICAL, 1 MAJOR, 4 MINOR); 0 target edits per Round 2 verification-only constraint" --> |
| YYYY-MM-DD | Round 3 resolution pass complete | <!-- e.g. "All 5 Round 2 findings resolved in target; JA-2 PARTIAL closed via N-1 fix; ship-ready" --> |

## Appendix: Files Read

<!-- Every source file inspected during the audit, with what was checked.
     Single best proof of audit thoroughness — anyone reading can see what was covered.

     Group by category. Categorization pattern from the gold-standard audit:
       Primary subject / Authoritative requirements / Research / Plan doc /
       Existing code / Schemas / Jira issues -->

**Primary subject:**

- `<path>` (<!-- "full" / lines -->) — <!-- what was checked -->

**Authoritative requirements (specs / standards / source-of-truth docs):**

- `<path>` — <!-- e.g. "§3.6 (integration schemas), §4.0 (lifecycle states), §4.1 (33 user stories) — full read" -->

**Research / evidence base:**

- `<path>` — <!-- e.g. "R-14: read findings and source citations, not just conclusions" -->

**Plan / design docs:**

- `<path>` — <!-- e.g. "Approval Log + Phase 4 only" -->

**Existing code:**

- `<path>:<lines>` — <!-- e.g. "ANALYZE_SKILL_SET at line 5; cross-verified against design claim" -->

**Schemas:**

- `<path>` — <!-- e.g. "verified additionalProperties: false at top level (line 7)" -->

**Jira issues (read issue AND all comments):**

- [RSMCLD-NNN](https://jira.bms.com/browse/RSMCLD-NNN) — <!-- AC source / what was verified -->

## Appendix: Commands Run

<!-- Every non-trivial bash command executed during the audit, in order.
     Reproducibility safeguard — re-running these on the same commit should produce
     the same findings. -->

```bash
# Example commands:
# rg "state.get\(" src/aido/nodes/ -l
# pytest tests/test_synthesis.py -v
# git log --oneline docs/plans/foo-plan.md | head -10
# grep -c "Wilson CI" docs/designs/cld-hypothesis-update-design.md
```

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

<!-- Role-identity prompt for spawning an Opus / Sonnet sub-agent to perform the
     Round 1 audit. Pattern modeled on .claude/agents/auditor.md and the gold-standard
     audit's Round 1 prompt. Use this verbatim as the prompt body when calling Agent()
     to delegate the audit. Replace ALL-CAPS placeholders before use. -->

**Model:** <!-- Opus / Sonnet -->

**Date:** YYYY-MM-DD

```text
You are a ROLE-DOMAIN-EXPERT (e.g. "senior pharmaceutical AI systems architect with deep
hands-on expertise in Bayesian statistics, DMTA loop design, HTS drug discovery informatics, and
GxP compliance engineering" / "principal staff engineer specializing in developer experience and
AI-augmented software development at scale"). You have shipped production systems in this domain
and you know the gap between what looks rigorous on paper and what actually holds up. You call out
that gap directly. When you cannot verify a claim, you say so explicitly rather than waving past
it. Your judgment is the product, not a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous. (If the orchestrating workflow
instead wants findings reported-not-applied, follow that override: write only the audit doc and
tag each finding [AUTO-FIX] / [NEEDS-DECISION].)

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix.

## Your Task

Audit TARGET-ARTIFACT at TARGET-COMMIT against SCOPE-DEFINITION on the following validation
dimensions:

  1. Internal Consistency (IC-N): does the target contradict itself? Cross-reference every section
     against every other.
  2. Spec / AC Compliance (JA-N): does it satisfy every authoritative requirement (spec, Jira ACs,
     template conformance, AC writing practices)?
  3. Domain Validity (DV-N): are the algorithmic / scientific / engineering choices defensible
     against the cited research / the live code?
  4. Independent Findings (F-N, open scope): surface anything else that matters — missing
     prerequisites, undocumented assumptions, contradictions with existing code, regulatory gaps.
     Use your own senior judgment and follow any lead the audit turns up.

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line).

For each finding, supply:
  - File:line reference (or doc-section).
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

You MUST run a Verification Matrix on at least 5-10 of your own findings: re-run the verification
command and record the actual output. A finding without a reproduced verification command is a
hypothesis, not a fact.

## Code-review scope (lean toward over-reading)

Read all source code, schemas, configuration, prompts, and tests that the target artifact
references, modifies, replaces, extends, makes claims about, or proposes new behavior next to.
This is a completeness requirement, not a sampling exercise.

**Bias toward reading too much code rather than too little.** It is much better to read code that
turns out to be irrelevant than to miss code that contains a finding the audit should have
surfaced. If you are uncertain whether a file is relevant: read it.

For every symbol / function / class / module / config key / CLI command the target references, run
`grep -r <symbol> <src-roots>` to surface every call site and every related file. Add anything
that surfaces to your read list before producing findings. Repeat for sibling / neighbor code that
implements the same pattern the target proposes (existing graph nodes if it proposes a new node;
existing CLI commands if it proposes a new command; existing prompts in the same directory).

Record every file you read in `## Appendix: Files Read`, grouped by category.

## Files to read (read in full, do not skim — and expand this list during the run)

The list below is a starting set. During the audit, expand it with anything that turns up from the
grep / pattern-consistency reads above. Better to add 10 files that turn out irrelevant than miss
1 file that contained a finding.

If a file is missing from the active worktree, search sibling worktrees at `.claude/worktrees/*/`
and read it from there before concluding it is missing.

### Audit format (read FIRST — this is how to WRITE the audit)

0. The audit template itself. Read it before writing anything so your output matches the required
   structure: the multi-round append-only model, the finding-ID taxonomy (IC-N / JA-N / DV-N /
   F-N / N-N / AD-N), the severity terminology, and the Decisions / Outstanding Issues /
   Already-Correct / Sign-Off / Verification-Matrix / Audit-Log / Appendix sections. **Look for
   `docs/audits/TEMPLATE.md` in this repo first; if it is not present (not every repo has merged
   the latest template to its main yet), read the canonical copy at
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`.** Conform your output to it.

### Primary subject

1. TARGET-PATH — the artifact under audit (read in full, first).

### Authoritative requirements

2. PATH — spec / standard / source-of-truth sections to read.

### Research / evidence base

3. PATH — what to look for (read findings + citations, not just conclusions).

### Plan doc / design doc (parent context)

4. PATH — what to verify.

### Existing code / schemas / prompts / configs / tests (lean toward over-listing)

List every source file, schema, config, prompt, or test the target references, modifies, replaces,
extends, or makes claims about — plus sibling / neighbor files for pattern-consistency review.
When in doubt: include it. Expand this list during the run.

5. PATH — what the target claims about it (or "sibling pattern for X").
... — extend as needed; no upper bound

### Jira issues (read issue AND all comments)

N. [JIRA-KEY](URL) — AC source (use the next number after the last code entry).

## Output

Write findings into this audit doc following the Round 1 section structure:
  R1 Summary → R1 Findings (IC / JA / DV / F tables + detailed F-N subsections) → R1 Resolution
  Pass → R1 Verification Matrix → AD-N entries in "Decisions Requiring Team Input" if any →
  Already-Correct Items.

Append a row to the Audit Log when done. Update the Status Summary's cross-round counts and
ship-readiness verdict.

Never fabricate evidence to satisfy a section. Empty findings sections are honest if nothing was
found; faked findings are not. Cite file:line for every codebase claim.

## Anti-patterns (avoid)

- Code-only check that ignores Approval Logs in linked plan docs.
- Frontmatter-only status check that ignores doc-body completion signals.
- Skipping linked docs and trusting the parent-doc description alone.
- Partial read of long docs — Approval Logs and sign-off are at the bottom.
- Inline fixes without commit hashes recorded in Resolution Pass.
- Empty Already-Correct Items list (the audit's credibility depends on it).
- Verification commands that aren't actually run.
- Under-reading the codebase — read sibling / neighbor files for pattern-consistency, not just the
  named symbols. A missed finding because you didn't read a relevant file is the audit's most
  serious failure mode.
```

### Round 2 Reviewer Prompt (Re-audit)

<!-- Round 2 is a VERIFICATION pass, not a fresh audit. Different prompt, different
     constraints. The gold-standard pattern: ideally a different agent / model than
     Round 1 to get an independent verification (auditor bias safeguard). -->

**Model:** <!-- Opus / Sonnet -->

**Date:** YYYY-MM-DD (post-Round-1)

```text
You are a ROLE-DOMAIN-EXPERT (same domain as Round 1, ideally a fresh agent / model for
independent verification). You are reading the Round 1 audit of TARGET-ARTIFACT — see the Round 1
Reviewer Prompt above in this same audit doc. This is the Round 2 verification pass.

Your task is to verify that EVERY Round 1 finding (IC-N / JA-N / DV-N / F-N) and EVERY AD-N decision
has been correctly applied to the target. You will also surface NEW issues (N-N) that the Round 1
fixes themselves introduced.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target doc/code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for Round 3 to apply. The point of an append-only verification round
  is so the NEXT round can tell what was actually applied vs what the verifier patched.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each Round 1 finding:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option (not a different option, not a half-applied version).

For NEW issues: re-read the target sections Round 1 modified. Look for stale cross-references
introduced by Round 1, Resolution Pass claims that didn't actually land, Round 1 fixes that
introduced new contradictions, and draft-author scaffolding left over from the Round 1 edit pass.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → R2.1 IC/JA/DV verification table (PASS/FAIL/PARTIAL + evidence) → R2.2 F-N
  verification table → R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. The audit template — `docs/audits/TEMPLATE.md` in this repo, or (if not yet present)
   `~/projects/project-template/template/docs/audits/TEMPLATE.md` — so your Round 2 section follows
   the required append-only structure.
1. TARGET-PATH — the artifact being verified. Read every section Round 1 touched.
2. THIS AUDIT DOC — the Round 1 sections are your verification checklist.
3. RELATED-DOC-OR-CODE — consult only if a Round 1 claim about it might be contradicted by what the
   code/doc actually says.
```

<!-- /aido:region name="scope" -->

<!-- aido:region name="round_1_findings" kind="replaceable" -->

<!-- /aido:region name="round_1_findings" -->

<!-- aido:region name="audit_log" kind="append_only" -->

<!-- /aido:region name="audit_log" -->

<!-- aido:region name="appendix_reviewer_prompt" kind="immutable" -->

<!-- /aido:region name="appendix_reviewer_prompt" -->
