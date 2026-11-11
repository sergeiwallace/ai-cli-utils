---
title: session-name-registry-dolt audit
category: audit
tags: [audit]
status: stub
date: 2026-11-11
source: "aido-stub"
template_version: "audit-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
---
<!-- Canonical Jinja source: STUB.md.jinja. This direct-copy stub is retained for consumers that have not migrated to rendering it. -->

# session-name-registry-dolt audit

**Status:** stub

**Created:** 2026-11-11

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

<!-- FILL THIS IN as you write. The entries below are this stub's own placeholder sections, so
     they already resolve; as you add, rename, or remove a heading, update this list in the same
     edit. A ToC that lists headings the doc does not have, or omits ones it does, is worse than
     no ToC because it looks like working navigation.
     The doc's D5 (c) decision: list EVERY `## ` and EVERY `### ` heading in the real doc, with
     GitHub-style anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate
     in-window (incl. VS Code Remote-SSH). If all-`###` proves too noisy, fall back to D5 (a)
     "meaningful `###`", a deterministic OR-rule: include a `###` when it (1) has child `####`,
     (2) its body ≥ ~8-10 lines, (3) its parent `##` is allowlisted, or (4) matches a pattern.
     Hand-maintained: the not-yet-shipped `aido toc check` tool. Verify with
     `scripts/check_doc_toc.py <this-doc>`, which fails on an anchor that does not resolve.
     Manual `<!-- toc:skip -->` / `<!-- toc:include -->` override. -->
  <!-- AIH-52: every AD-N detail heading requires a ToC row; generate with
       `python3 scripts/check_doc_toc.py --fix-decision-rows <doc>`, linking each
       decision as `  - [AD-N: Name](#ad-N)`, the STABLE short id, NOT the heading auto-slug.
       AD-N headings carry a mutable status suffix (enumerated once, at TEMPLATE.md's AD-1
       skeleton) that CHANGES the auto-slug on resolution; the `<a id="ad-N"></a>` anchor is the
       durable jump target. -->

- [Scope](#scope)
- [Status Summary](#status-summary)
- [Audit Log](#audit-log)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

## Scope

### Founding ask and alignment

`founding_ask_ref`: [FILL: exact `canonical_root_json` and `founding_ask_ref_sha256` | N/A with reason]

[FILL: When this `founding_ask_ref` is `N/A` for a Tier 1 dispatch with no `canonical_root_json`, paste the literal `verbatim_exact_ask` from the worker brief directly below in a fenced `text` block. Never replace it with a summary or a reference to the transient brief. Do not add this block for Tier 0 or Tier 2/3.]

Read every authoritative raw FAR entry, including each re-scope entry. The requirements index is
non-authoritative; report any omitted, dropped, narrowed, broadened, rejected, deferred, or
inverted entry as a canonical ordinary `JA-N` `open-blocking` finding. Compare raw ask and target
artifact during every round, including every AI-resolved Decision and AD-N: an AI decision never
drops, narrows or contradicts an explicit founding-ask requirement, so a Decision that does is a
`JA-N` `open-blocking` finding even when its coverage row names it (decision-framework.md § Gate 0;
run `scripts/far_fidelity.py check --doc <target>`). Exact successor-brief authorization occurs only after convergence in
the driver-owned boundary finalizer; this document's rendering never authorizes a transition.

| Authoritative raw entry | Audit coverage |
|---|---|
| [FILL: one row per entry id] | [FILL: finding/evidence or explicit disposition] |

[What this audit is targeting: design doc, plan doc, shipped code, etc.]

<!-- /doc:region name="scope" -->

## Run Ledger

ledger-version: 1

| Field | Value |
|---|---|
| Stage cursor | `doc-audit active; round 1` |
| Promotion | `blocked` |
<!-- doc:region name="loop_receipt" kind="generated" -->
## Status Summary

**Loop state:** active — NOT promotion ready

| Loop | Round | Target digest | New CRITICAL | New MAJOR | New MINOR | Open blocking | Scope | Stable |
|---|---:|---|---:|---:|---:|---|---|---|
| doc-audit | 1 | sha256:464fe03d6180c5ffdc936d6a43c12e7174d4e69e3a4be1262b76cf2a3872c164 | 1 | 7 | 1 | round1:DV-1, round1:DV-2, round1:DV-3, round1:F-1, round1:IC-1, round1:IC-2, round1:JA-1, round1:JA-2, round1:JA-3 | full | no |
<!-- /doc:region name="loop_receipt" -->
<!-- doc:region name="round_1_findings" kind="replaceable" -->

<!-- ModeDependentKind: REPLACEABLE while round 1 is active; IMMUTABLE once
     the round is committed (validator infers from doc body state).
     Deliberately carries NO `## Round N` heading: the audit-loop driver owns
     round headings and refuses a carrier that already has one
     (scripts/audit_loop.py::_prepare_round). -->

(none yet)

<!-- /doc:region name="round_1_findings" -->

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|

<!-- /doc:region name="audit_log" -->

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

<!-- The reviewer prompt(s) below are the canonical scaffold (kept in sync with
     docs/audits/TEMPLATE.md). Replace the ALL-CAPS placeholders before launching the audit
     agent, and delete the Round 2 appendix if the audit will be single-round. Frozen at
     stub creation. -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** <!-- family and model: a family other than the author's (Codex for a Claude-written target, Claude for a Codex-written one); the same family only when no other route works, recorded as the weaker check; scripts/audit_loop.py and /audit-review route only to Codex today, so a Codex-written target reviewed through them is that weaker check -->

**Date:** 2026-11-11

```text
You are a ROLE-DOMAIN-EXPERT (e.g. "senior data-platform architect with deep hands-on
expertise in distributed systems, data-pipeline design, and reliability engineering" /
"principal staff engineer specializing in developer experience and AI-augmented software
development at scale"). You have shipped production systems in this domain
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

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line). Every AD-N Decision line
must be followed by TEMPLATE.md's fixed-key `decision-record` HTML comment: chosen-option and,
when AI-resolved, family, concrete model ID, effort, and profile/persona.
When a finding or AD-N recommends an externally sourced idea, tool, repo, or pattern, apply
`docs/procedures/source-vetting-and-corroboration.md` and record the evidence route or exploratory
status; a repository name alone is not proof of a best practice.

For each finding, supply:
  - File:line reference (or doc-section).
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding.
  - A specific recommended fix.

When the brief names a rubric, return one verdict per rubric row in scope. Give every verdict, a
pass included, the verbatim quote, screenshot path or measurement it rests on. A verdict with no
evidence is not a verdict.

Length is not a merit. Do not credit a document, a section or a finding for its length, and do not
credit prose for reading like your own. Model judges favour longer answers and their own family's
writing (Zheng et al. 2023), so judge each row on its evidence alone.

For driver-owned rounds, the prompt includes a JSON example generated directly from
`scripts.audit_receipt.EVENT_PAYLOADS` and its finding validator. Use that injected example as
the only payload contract; this canonical document intentionally carries no hand-maintained JSON
copy. The driver overwrites `scope` and `source_event_id` with mechanically
derived values before validation.

Append exactly one `### R<N> Receipt Payload Projection` heading followed by one fenced `json`
object. Do not emit an `intent_alignment` projection during a round: it cannot authorize a
transition until the exact successor brief exists. Findings use stable keys, lowercase severity, a
non-empty reachable behavior, and no per-member digest. When non-null, `cluster.spec_version`
and `cluster.invariant_id` must match `^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$`; for example,
`"job-feedback-taxonomy-curation-design@27a0571"` is a valid `spec_version`.

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
- Treating the run as done because the doc exists, is large, has a fresh mtime, or the command
  exited 0. An UNFILLED template passes all four (AIH-762: 42110 bytes, fresh, rc=0, and larger
  than several real audits) - grep for a finding heading, or run
  ai-harness/scripts/check_audit_doc_filled.py.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** <!-- family and model: a family other than the author's (Codex for a Claude-written target, Claude for a Codex-written one); the same family only when no other route works, recorded as the weaker check; scripts/audit_loop.py and /audit-review route only to Codex today, so a Codex-written target reviewed through them is that weaker check; ideally a fresh agent for independent verification -->

**Date:** 2026-11-11 (post-Round-1)

```text
You are a ROLE-DOMAIN-EXPERT (same domain as the prior rounds, ideally a fresh agent / model for
independent verification). You are reading the audit history of TARGET-ARTIFACT. This is a
later-round verification pass.

## Severity rubric (binding — your findings are validated against this)

CRITICAL — implemented literally as specified, this produces data loss, a security or
  safety-invariant violation, or an unrecoverable state, in a reachable scenario.
MAJOR — implemented literally as specified, this produces incorrect or unsafe behavior in a
  reachable scenario: a genuine contradiction between documents, an ambiguity with more than one
  plausible unsafe reading, or a stated invariant the spec as written does not enforce.

## Scope guard — full open MUST-fix backlog

`OPEN-MUST-FIX-BACKLOG` must be the full current list of every unresolved item marked **MUST be
fixed before merge** anywhere in the prior audit history, with its ID and latest verification
status. Your task is to verify that EVERY item in that backlog, including every applicable finding
(IC-N / JA-N / DV-N / F-N) and AD-N decision, has been correctly applied to the target. If the
filled list omits an older open MUST-fix item you find in the audit history, add it to your output;
do not accept a narrowed scope. The immediately preceding round's new findings are additive only,
never a replacement for the full backlog.

You will also surface NEW issues (N-N) that prior fixes themselves introduced. Any FAIL or PARTIAL
backlog item remains in the open MUST-fix backlog and must be carried into every subsequent
re-verification and fix scope until it passes.

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

For each open MUST-fix backlog item:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's implementation in the target and verify it matches
the chosen option (not a different option, not a half-applied version).

For NEW issues: re-read the target sections prior fixes modified. Look for stale cross-references
introduced by those fixes, Resolution Pass claims that didn't actually land, contradictions those
fixes introduced, and draft-author scaffolding left over from the edit pass.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → full open MUST-fix backlog verification table (PASS/FAIL/PARTIAL + evidence) →
  R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

For a driver-owned round, use the schema-generated payload example injected into the live prompt
and append exactly one findings projection. The driver supplies the current round, whole-target
digest, derived scope, complete backlog, and the validated founding-ask carrier. When non-null,
`cluster.spec_version` and `cluster.invariant_id` must match
`^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$`; for example,
`"job-feedback-taxonomy-curation-design@27a0571"` is a valid `spec_version`.

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. The audit template — `docs/audits/TEMPLATE.md` in this repo, or (if not yet present)
   `~/projects/project-template/template/docs/audits/TEMPLATE.md` — so your Round 2 section follows
   the required append-only structure.
1. TARGET-PATH — the artifact being verified. Read every section prior fixes touched.
2. THIS AUDIT DOC — its full history is your verification checklist; derive and check the complete
   `OPEN-MUST-FIX-BACKLOG`, not only the preceding round.
3. RELATED-DOC-OR-CODE — consult if a backlog claim about it might be contradicted by what the
   code/doc actually says.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->

## Round 1 — Full Audit

**Round 1 auditor:** Codex independent audit

**Round 1 date:** 2026-11-11

**Round 1 scope:** Full design audit of `docs/designs/session-name-registry-dolt.md` at
`962e543cc89d9cfc50d65f698155d5ae6081162f`, against founding ask A001, the converged research,
the current registry and callers, the approval-status branch, and relevant git history. No target
or source file was modified.

### R1 Summary

Nine findings are open: 1 CRITICAL, 7 MAJOR, and 1 MINOR. The design carries the requested general
and scoped verdict model, searchable tables, Dolt placement, and the unique-claimant rule. It is not
implementation-ready: the freeze leaves the old offline-spare writer live, the proposed SQL script
does not actually bind guards to mutations, the lifecycle rejects existing label/reassignment
operations and omits migration recovery, the timeout contract is stale, SQLite does not enforce the
claimed common constraints, and the seed AC can pass without the explicitly approved seed.

### R1 Rubric Verdicts

| Rubric row | Verdict | Evidence |
|---|---|---|
| Current-registry and approval-branch parity is carried into ACs | **FAIL (CONFIRMED)** | The blanket T-1.4 AC at `docs/designs/session-name-registry-dolt.md:470-471` does not enumerate the listed behaviours. AC-SM-HOLD omits `reserve-label`, reassignment, failed-migration release and partial-migration recovery even though `src/ai_cli/name_registry.py:1262,1856,2032,2049` implement them. See IC-2 and JA-2. |
| Claim protocol gives exactly one winner, including identical payloads | **PASS for claims (CONFIRMED)** | T-1.2 requires a unique claimant and 20 identical-value trials at `docs/designs/session-name-registry-dolt.md:459-464`; steps 5-7 forbid merge/force and require remote read-back at lines 252-258. IC-1 is a separate contradiction caused by applying that claim-only read-back rule to every non-claim write. |
| Cutover cannot leave two stores accepting writes | **FAIL (CONFIRMED)** | T-4.1 freezes only clients that read schema 2, while the retained `take_spare` path reads the last fetched tip without network and durably spools an activation (`src/ai_cli/name_registry.py:1350-1385`). The design also promises that spares cover offline launches at line 593. See DV-2. |
| Dolt and SQLite share one schema | **FAIL (CONFIRMED)** | The design claims “same tables and constraints” at lines 322-325 and 813-821, but translates JSON to unconstrained TEXT at lines 408-410 and never requires SQLite foreign-key enforcement. See DV-3. |
| Every AC is independently testable and falsifiable | **FAIL (CONFIRMED)** | T-1.4 delegates all listed parity to one catch-all criterion, and T-4.1 quantifies over “every client older than this release” without defining a finite supported-version set or test matrix. JA-2 records the concrete migration behaviours this leaves unpinned. |
| Public document contains no private host, session, tool or project name | **FAIL (CONFIRMED)** | No literal private host or session name was found, but the document embeds a private project/task identifier and orchestration/model provenance at lines 6-8, 35, 45 and throughout decision metadata. See F-1. |

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Severity | Verdict | Evidence |
|---|---|---|---|
| IC-1 | MAJOR | FAIL (CONFIRMED) | “Every write follows one loop” at line 241 ends with a required attempt-claimant read-back at lines 256-258, but only `holds` has a `claimant` column (line 389); status/verdict rows and deletions cannot satisfy it. |
| IC-2 | MAJOR | FAIL (CONFIRMED) | AC-SM-HOLD says unlisted events reject (line 217), yet the preserved namespace includes label tokens and `reassign-name` (lines 281, 299-300); current reachable implementations are at `src/ai_cli/name_registry.py:1262-1290,1856-1877`. |

#### Spec / AC Compliance (JA-N)

| ID | Severity | Verdict | Evidence |
|---|---|---|---|
| JA-1 | MAJOR | FAIL (CONFIRMED) | The design assigns every Dolt call the old `publish_timeout_s` at line 272. Current code instead reads separate `registry_fetch_timeout_seconds` and `registry_push_timeout_seconds` on every operation (`src/ai_cli/config.py:549-556`; `src/ai_cli/name_registry.py:538-564,545-548,644`). |
| JA-2 | MAJOR | FAIL (CONFIRMED) | “intent ... completion ... idempotent recovery” is claimed at lines 294-295, but AC-SM-HOLD has only intent and complete transitions (lines 209, 212). Current no-survivor rollback, survivor marking and intent recovery are separate reachable behaviours at `src/ai_cli/name_registry.py:2032-2068` and `src/ai_cli/migrate_names.py:128-193`. |
| JA-3 | MAJOR | FAIL (CONFIRMED, ASK DRIFT) | A001 requires a concrete replacement plus an explicitly approved migration set. T-3.2 only applies whatever an external file happens to list (lines 535-538), so an empty or wrong seed satisfies the AC. |

#### Domain Validity (DV-N)

| ID | Severity | Verdict | Evidence |
|---|---|---|---|
| DV-2 | CRITICAL | FAIL (CONFIRMED) | The freeze says the old branch receives no more commits (line 556), yet offline spares remain usable (lines 290-291, 593) through a cached-tip durable spool. An old client can activate a spare locally and a new client can activate the same imported spare in Dolt. |
| DV-1 | MAJOR | FAIL (CONFIRMED) | Step 3 says one SQL script runs guard queries followed by plain DML (lines 247-251). A preceding `SELECT` does not condition or abort later `INSERT`/`UPDATE`/`DELETE`, so release, ownership and state guards are not enforced as written. |
| DV-3 | MAJOR | FAIL (CONFIRMED) | SQLite foreign keys default off, and the proposed TEXT translation has no `json_valid` CHECK. The two backends therefore accept different rows despite the shared-constraint claim. |

#### Independent Findings (F-N)

| ID | Severity | Verdict | Evidence |
|---|---|---|---|
| F-1 | MINOR | FAIL (CONFIRMED) | Public design lines 6-8, 35, 45 and decision metadata expose a private task/project identifier and internal workflow/model provenance, contrary to the repository's public-package rules. |

#### DV-2: The frozen cutover still permits old offline-spare writes — `CRITICAL`

**Location:** `docs/designs/session-name-registry-dolt.md:290-291,555-563,593`;
`src/ai_cli/name_registry.py:1350-1418`

**Exact evidence:**

> `When the frozen snapshot is published, every client older than this release shall refuse every allocation`
>
> `Old clients fail closed; spares cover offline launches`

Current `take_spare` explicitly “Reads the last fetched tip, never the network” and writes a durable
pending activation. Such a client never observes schema 2. After import, Dolt still sees that token
as the machine's unactivated spare, so a new client can activate it again for another session.

**Why it matters:** This violates the fleet-wide uniqueness safety invariant during the exact
window D-7 claims has only one writer. The later old-spool publication may refuse, but two sessions
can already be running under the same token.

**Verification command:**

```bash
rg -n "spares cover offline|old branch shall receive no further commit|take_spare|Reads the last fetched tip|_write_spool_entry" docs/designs/session-name-registry-dolt.md src/ai_cli/name_registry.py
```

**Recommended fix:** Before import, disable old offline allocation, inventory and publish or import
every pending spool entry, and prove every old process is stopped or fenced. Add a cutover AC that
starts an old client from a cached schema-1 tip and proves it cannot activate a spare after the
freeze; only then flip the backend.

#### DV-1: The proposed one-script guard is not an atomic guard — `MAJOR`

**Location:** `docs/designs/session-name-registry-dolt.md:241-251`

**Exact evidence:**

> `Run the guard and the write as one dolt sql script: the guard queries, then plain INSERT, UPDATE or DELETE statements`

A SQL result set has no control-flow effect on following statements. As specified, a failed
ownership, version or state predicate can still be followed by unconditional DML. Primary keys
protect only some inserts; they do not protect guarded updates or deletes.

**Why it matters:** A literal implementation can release a changed row, edit another machine's
hold, or apply an invalid transition even though its guard query returned false.

**Verification command:**

```bash
sed -n '241,258p' docs/designs/session-name-registry-dolt.md
```

**Recommended fix:** Specify one transaction whose DML contains every guard in `WHERE`/conditional
`INSERT ... SELECT`, validate affected-row counts, and abort before `dolt_commit` unless exactly the
expected rows changed. Add negative tests for every guarded update/delete, not only claims.

#### IC-1: The claimant read-back cannot validate every write — `MAJOR`

**Location:** `docs/designs/session-name-registry-dolt.md:241-258,345-400`

**Exact evidence:**

> `Every write follows one loop.`
>
> `Only a row carrying this attempt's claimant counts as a win.`

Only `holds` defines `claimant`. `names`, `verdicts`, `migrations`, and deletes have no per-attempt
receipt row. One plausible implementation skips read-back for them; another treats a successful
commit as failure and retries an already-landed mutation.

**Why it matters:** Status, verdict, migration-completion and release callers cannot implement the
stated success rule consistently, which can yield false failure or duplicate retries after a
successful push.

**Verification command:**

```bash
rg -n "Every write follows|Only a row carrying|create table (names|verdicts|migrations)|claimant" docs/designs/session-name-registry-dolt.md
```

**Recommended fix:** Split a general transaction protocol from the claim protocol. Use push
compare-and-swap plus commit-id confirmation for ordinary writes; reserve unique claimant
read-back for allocations, and specify idempotency/read-back for deletes and status changes.

#### IC-2: The hold state machine rejects preserved label and reassignment operations — `MAJOR`

**Location:** `docs/designs/session-name-registry-dolt.md:193-221,276-300,372-391`;
`src/ai_cli/name_registry.py:92-94,1262-1290,1856-1877`

**Exact evidence:**

> `Event alphabet: top-up, claim, migration-intent, activate-spare, migrate-to-own-spare, migration-complete, release, spare-release, foreign-edit`
>
> `any event not listed for that state ... reject with an invalid-transition refusal`

The alphabet has no `reserve-label` or `reassign` event despite the parity list preserving both.
The database also applies `^[a-z]+$` to every hold token, while current label tokens intentionally
allow embedded digits when the token is not all digits.

**Why it matters:** Literal conformance either breaks reachable drop-label launches and ownership
repair or violates the state-machine AC to keep them working.

**Verification command:**

```bash
rg -n "Event alphabet|any event not listed|label tokens|reassign-name|_LABEL_TOKEN_RE|def reserve_label|def reassign_name" docs/designs/session-name-registry-dolt.md src/ai_cli/name_registry.py
```

**Recommended fix:** Add label-held and reassignment transitions with guards and negative cases;
preserve the current label-token language in the schema; and include their existing tests in the
backend parity matrix.

#### JA-1: The timeout design regresses two shipped reliability fixes — `MAJOR`

**Location:** `docs/designs/session-name-registry-dolt.md:263-274`;
`src/ai_cli/config.py:549-556`; `src/ai_cli/name_registry.py:538-564,545-548,636-653`

**Exact evidence:**

> `Each call has a timeout taken from the existing publish_timeout_s policy value.`

The current implementation instead has separate fetch and push budgets, each read fresh from user
configuration. Git history confirms these were deliberate fixes in commits `0bb115b1` and
`52a9412b`, after fetches exceeded the former 15-second publish budget.

**Why it matters:** Applying one stale budget can make ordinary reads report the registry
unreachable on a slow host and unnecessarily consume offline spares—the already-fixed failure
mode.

**Verification command:**

```bash
rg -n "publish_timeout_s|registry_fetch_timeout_seconds|registry_push_timeout_seconds|fetch_timeout_s|push_timeout_s" docs/designs/session-name-registry-dolt.md src/ai_cli/config.py src/ai_cli/name_registry.py
```

**Recommended fix:** Preserve separate fresh-read fetch and push settings (and specify budgets for
version/query/commit calls), carry their failure messages, and add parity ACs using distinct values.

#### JA-2: Migration failure and recovery parity has no acceptance contract — `MAJOR`

**Location:** `docs/designs/session-name-registry-dolt.md:193-223,294-295,470-471`;
`src/ai_cli/name_registry.py:1936-2068`; `src/ai_cli/migrate_names.py:102-193`

**Exact evidence:**

> `the migration ledger: intent in the reserve commit, completion with the old row's release, idempotent recovery`

The state machine specifies only intent and successful completion. It omits the current atomic
release of a failed migration with no survivors, marking survivor metadata as
`migration-incomplete`, recovery from artifacts under old/new/both titles, and the completion-push
failure path. It also makes every intent immediately `migration-incomplete`, while the carried
validator says `recovery` is set exactly in that state.

**Why it matters:** A literal implementation can leak a name after a clean rollback, lose recovery
evidence after a partial rename, or fail to resume an interrupted migration idempotently.

**Verification command:**

```bash
rg -n "idempotent recovery|migration-intent|migration-complete|release_failed_migration|mark_migration_incomplete|recover_intents|recovery is set exactly" docs/designs/session-name-registry-dolt.md src/ai_cli/name_registry.py src/ai_cli/migrate_names.py
```

**Recommended fix:** Add explicit events/transitions and EARS ACs for clean failure, partial
failure, completion-push failure, and each recovery observation. Map each current migration test to
Dolt and SQLite rather than relying on T-1.4's blanket sentence.

#### JA-3: The seed AC does not require the founding ask's approved set — `MAJOR`

**Location:** founding ask A001, original request lines 22-24 and earlier-message line 37;
`docs/designs/session-name-registry-dolt.md:25-39,531-538,1022-1025`

**Exact evidence:**

> `When seeded, the system shall apply every listed general status and scoped verdict`

That verifies application of a file, not that the file contains the explicit replacement,
approved-to-migrate names, and earlier pre-approved set. An empty seed passes. The open question
admits part of the list is still owed, so the design has not grounded its claimed coverage.

**Why it matters:** The implementation can satisfy every published AC while dropping explicit
owner approvals and starting intended names as `unreviewed`. This is a direct narrowing of A001 and
is blocking under the alignment obligation.

**Verification command:**

```bash
rg -n "replacement name|approved to migrate|pre-approved|T-3.2 Seed|apply every listed|still owed" docs/designs/session-name-registry-dolt.md
```

**Recommended fix:** Add a private, digest-bound seed manifest derived from the authoritative ask
and recovered feedback, then require exact-set verification (including the replacement and
migration approvals) before cutover. The public design can cite the manifest digest without
publishing private names.

#### DV-3: SQLite does not enforce the promised common constraints — `MAJOR`

**Location:** `docs/designs/session-name-registry-dolt.md:322-330,339-410,456-458,813-821`

**Exact evidence:**

> `with the same tables and constraints`
>
> `The SQLite form replaces enum with a check (... in (...)), json with text`

SQLite foreign keys are disabled by default per connection; the design never requires
`PRAGMA foreign_keys=ON`. Replacing JSON with TEXT without `CHECK(json_valid(...))` also accepts
values the Dolt JSON type rejects. The local probe returned `foreign_keys=0` and
`invalid_json=0`.

**Why it matters:** Public installs can accumulate orphan verdicts or malformed policy/recovery
values that the Dolt backend refuses, so backend parity and later migration are not reliable.

**Verification command:**

```bash
python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); print('foreign_keys='+str(c.execute('pragma foreign_keys').fetchone()[0])); print('invalid_json='+str(c.execute(\"select json_valid('not-json')\").fetchone()[0]))"
```

**Recommended fix:** Require and assert `PRAGMA foreign_keys=ON` on every SQLite connection, add
`json_valid` checks for JSON-shaped TEXT columns, and parity-test orphan FKs, invalid JSON, enum,
case/token and per-kind hold constraints on both engines.

#### F-1: The public design exposes private workflow identifiers — `MINOR`

**Location:** `docs/designs/session-name-registry-dolt.md:6-8,35,45,663-664,1048` and analogous
decision metadata

**Exact evidence:**

> `task:`
>
> `Codex is cross-family review only`

No private host or session literal was found, but the private project/task identifier and internal
orchestration/model trail are not general-audience design content.

**Why it matters:** The repository's public-package rules prohibit private project prefixes and AI
workflow tells in outward-facing artifacts.

**Verification command:**

```bash
rg -n "^task:|decision-id=|ai-family=|Opus|Codex" docs/designs/session-name-registry-dolt.md
```

**Recommended fix:** Remove private task/project identifiers and process narration from the public
design; retain only generic decision rationale and public product names when technically necessary.

### R1 Resolution Pass

| Finding | Status | Required resolution |
|---|---|---|
| DV-2 | UNRESOLVED — BLOCKING | Fence cached/offline old clients and pending spools before import/flip; add the cross-version cutover test. |
| DV-1 | UNRESOLVED — BLOCKING | Define guarded conditional DML with affected-row/abort semantics and negative tests. |
| IC-1 | UNRESOLVED — BLOCKING | Separate ordinary transaction confirmation from claim ownership read-back. |
| IC-2 | UNRESOLVED — BLOCKING | Extend the lifecycle and schema for labels and reassignment. |
| JA-1 | UNRESOLVED — BLOCKING | Preserve separate fetch/push timeout contracts and tests. |
| JA-2 | UNRESOLVED — BLOCKING | Specify every migration failure/recovery transition and map tests. |
| JA-3 | UNRESOLVED — BLOCKING | Bind the seed AC to the authoritative approved set. |
| DV-3 | UNRESOLVED — BLOCKING | Enforce common FK/JSON/per-kind constraints on SQLite. |
| F-1 | UNRESOLVED — BLOCKING | Remove private workflow identifiers from the public artifact. |

No inline fix was applied; this read-only delivery changes only the audit document through the
wrapper.

### R1 Verification Matrix

| Finding | Re-run command | Expected | Actual | Result |
|---|---|---|---|---|
| DV-2 | `rg` freeze/spare/take-spare/spool evidence | Freeze plus no old allocation path | Freeze promise and “spares cover offline” coexist; `take_spare` reads cached tip and calls `_write_spool_entry` | ✅ reproduced |
| DV-1 | `sed -n '241,258p'` | Guard controls mutation | Text says guard queries, then plain DML; no conditional/affected-row rule | ✅ reproduced |
| IC-1 | `rg` every-write/claimant/tables | Receipt mechanism for every write | Only `holds.claimant` exists; rule is stated for every write | ✅ reproduced |
| IC-2 | `rg` event alphabet/label/reassign/current symbols | Every preserved operation in lifecycle/schema | No label/reassign event; T11 rejects unlisted events; current functions exist | ✅ reproduced |
| JA-1 | `rg` timeout symbols across design/code | Preserve distinct shipped timeouts | Design has one `publish_timeout_s`; code has fresh-read fetch and push settings | ✅ reproduced |
| JA-2 | `rg` migration recovery symbols | ACs cover current recovery branches | Design has intent/complete only; current code has release, incomplete marking and recovery | ✅ reproduced |
| JA-3 | `rg` seed coverage/AC | Exact approved set pinned | AC applies whatever is listed; design says part of list is still owed | ✅ reproduced |
| DV-3 | in-memory SQLite probe plus DDL `rg` | FK and JSON constraints active | `foreign_keys=0`, `invalid_json=0`; design says JSON becomes TEXT | ✅ reproduced |
| F-1 | `rg` private task/model strings | No private workflow identifiers | Private task identifier and model/orchestration strings found | ✅ reproduced |

**Verified: 9/9 findings reproduced against design commit `962e543c` and current code at the same
HEAD.** The failed `uv` cache initialization in this read-only environment was not treated as a
repository finding; the SQLite probe used the installed standard-library interpreter and an
in-memory database.

### Decisions Requiring Team Input

None. Each required correction follows from an explicit invariant, current behavior, or public
repository rule; no AD-N choice is needed.

### Outstanding Issues

All nine findings are open and blocking; their required fixes are enumerated in the Resolution Pass.

### Already-Correct Items

- **CONFIRMED:** The normalized `names` plus `verdicts` model represents general status and scoped
  repository/host/session approvals and rejections with actor, recorder, time and reason
  (`docs/designs/session-name-registry-dolt.md:345-370`).
- **CONFIRMED:** Reserved/in-use/migrated vocabulary is represented by `holds` and `migrations`, and
  T-3.4 supplies a searchable SQL/JSON/history surface (lines 372-401, 541-544).
- **CONFIRMED:** The claim-specific identical-value hazard is addressed by a unique per-attempt
  claimant, no merge after rejected push, and remote-tip read-back (lines 247-258, 459-464).
- **CONFIRMED:** New public CLI options in the design have both short and long forms, satisfying the
  repository CLI convention (lines 486-490, 531, 535, 539, 541-544).
- **CONFIRMED:** Placement on a private repository ref, info-branch suppression, telemetry disable,
  missing-binary refusal and a version floor carry the research's principal Dolt conditions.
- **CONFIRMED:** No literal private host name or session name appears in the design.

### R1 Sign-Off

**Verdict:** NOT READY. Promotion is blocked on all nine findings; DV-2 is a critical uniqueness
safety violation.

### Appendix: Files and Artifacts Read

**Audit format and carrier (full):**

- Canonical `docs/audits/STUB.md.jinja`
- `docs/audits/session-name-registry-dolt-audit.md`
- Founding ask A001 resolved record

**Primary and research (full):**

- `docs/designs/session-name-registry-dolt.md`
- `docs/research/session-name-registry-storage.md`

**Ground-truth code (full or focused symbol/caller reads):**

- `src/ai_cli/name_registry.py` (full)
- `src/ai_cli/name_commands.py`, `src/ai_cli/migrate_names.py`, `src/ai_cli/name_prompt.py`,
  `src/ai_cli/session_naming.py`
- Relevant callers in `src/ai_cli/main.py`, plus timeout definitions in `src/ai_cli/config.py`

**Tests, prior work and history:**

- Relevant sections of `tests/test_name_registry.py`, `tests/test_name_release.py`,
  `tests/test_session_title.py`, `tests/test_session_name_launch.py`, and `tests/name_fleet.py`
- Approval-status branch: `docs/designs/session-name-approval-status.md`,
  `tests/test_session_name_status.py`, and shipped status seed counts
- Git history for commits `0bb115b1`, `52a9412b`, `5a1a90b2`, `5192e13e`, `35a96b40`, and
  `962e543c`; all registry/name symbol call sites under `src/` and `tests/` were enumerated with
  `rg` before findings were finalized.

### R1 Receipt Payload Projection

```json
{
  "findings": [
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "cutover", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:DV-2",
      "fingerprint": "fp:cutover-offline-spool-dual-writer",
      "id": "DV-2",
      "reachable_behavior": "An old cached client and a new Dolt client can activate the same imported spare during cutover.",
      "severity": "critical",
      "submitted_severity": "critical"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "dolt-write-protocol", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:DV-1",
      "fingerprint": "fp:sql-select-does-not-guard-dml",
      "id": "DV-1",
      "reachable_behavior": "A failed ownership or state guard can still be followed by unconditional mutation.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "transaction-protocol", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:IC-1",
      "fingerprint": "fp:claimant-readback-not-general",
      "id": "IC-1",
      "reachable_behavior": "A successful status, verdict, migration or delete write cannot satisfy the specified claimant read-back rule.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "hold-lifecycle", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:IC-2",
      "fingerprint": "fp:lifecycle-omits-label-reassign",
      "id": "IC-2",
      "reachable_behavior": "A label reservation or ownership reassignment is rejected by the literal lifecycle contract.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "timeout-policy", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:JA-1",
      "fingerprint": "fp:separate-timeouts-dropped",
      "id": "JA-1",
      "reachable_behavior": "A normal fetch can time out under the obsolete shared publish budget and consume offline capacity.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "migration-recovery", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:JA-2",
      "fingerprint": "fp:migration-recovery-parity-unpinned",
      "id": "JA-2",
      "reachable_behavior": "A failed or interrupted migration can leak a hold or lose survivor recovery state.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": "A001", "spec_version": "audit-loop@schema-v1", "subsystem": "seed-alignment", "violation_kind": "ask-drift"},
      "disposition": "open-blocking",
      "finding_key": "round1:JA-3",
      "fingerprint": "fp:authoritative-approved-seed-not-bound",
      "id": "JA-3",
      "reachable_behavior": "The cutover can seed an empty or wrong approval set while every design AC passes.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "sqlite-backend", "violation_kind": "incorrect-behavior"},
      "disposition": "open-blocking",
      "finding_key": "round1:DV-3",
      "fingerprint": "fp:sqlite-constraints-not-parity",
      "id": "DV-3",
      "reachable_behavior": "The SQLite backend can accept orphan verdicts and malformed JSON-shaped values that Dolt rejects.",
      "severity": "major",
      "submitted_severity": "major"
    },
    {
      "cluster": {"invariant_id": null, "spec_version": "audit-loop@schema-v1", "subsystem": "public-document", "violation_kind": "public-artifact-hygiene"},
      "disposition": "open-blocking",
      "finding_key": "round1:F-1",
      "fingerprint": "fp:public-design-private-workflow-identifiers",
      "id": "F-1",
      "reachable_behavior": "A public package artifact exposes private workflow identifiers and internal orchestration provenance.",
      "severity": "minor",
      "submitted_severity": "minor"
    }
  ],
  "major_nonblocking_justifications": {},
  "new_by_severity": {"critical": 1, "major": 7, "minor": 1},
  "scope": {
    "backlog": [],
    "diff_since": null,
    "diff_summary": {
      "changed": false,
      "from": null,
      "to": "sha256:464fe03d6180c5ffdc936d6a43c12e7174d4e69e3a4be1262b76cf2a3872c164"
    },
    "kind": "full"
  },
  "source_event_id": "round-1-audit"
}
```
