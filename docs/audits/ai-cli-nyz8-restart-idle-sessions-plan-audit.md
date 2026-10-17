---
title: ai-cli-nyz8-restart-idle-sessions-plan audit
category: audit
tags: [audit]
status: findings-pending-fix
date: 2026-10-17
source: "aido-stub"
template_version: "audit-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
---
<!-- Canonical Jinja source: STUB.md.jinja. This direct-copy stub is retained for consumers that have not migrated to rendering it. -->

# ai-cli-nyz8-restart-idle-sessions-plan audit

**Status:** findings-pending-fix

**Created:** 2026-10-17

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
  <!-- AIH-52: when the audit grows a "Decisions Requiring Team Input" section, link each
       decision as `  - [AD-N: Name](#ad-N)`, the STABLE short id, NOT the heading auto-slug.
       AD-N headings carry a mutable status suffix (enumerated once, at TEMPLATE.md's AD-1
       skeleton) that CHANGES the auto-slug on resolution; the `<a id="ad-N"></a>` anchor is the
       durable jump target. -->

- [Scope](#scope)
  - [Founding ask and alignment](#founding-ask-and-alignment)
- [Run Ledger](#run-ledger)
- [Status Summary](#status-summary)
- [Audit Log](#audit-log)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)
- [Round 1 — Full Audit](#round-1--full-audit)
  - [R1 Summary](#r1-summary)
  - [R1 Findings](#r1-findings)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
  - [R1 Receipt Payload Projection](#r1-receipt-payload-projection)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Round 2 — Verification](#round-2--verification)
  - [R2 Summary](#r2-summary)
  - [R2.1 Round 1 IC/JA/DV verification](#r21-round-1-icjadv-verification)
  - [R2.2 Round 1 F-N verification](#r22-round-1-f-n-verification)
  - [R2.3 AD-N decisions verification](#r23-ad-n-decisions-verification)
  - [R2.4 NEW issues surfaced](#r24-new-issues-surfaced)
  - [R2 Resolution Pass](#r2-resolution-pass)
  - [R2 Verification Matrix](#r2-verification-matrix)
  - [R2 Recommendations](#r2-recommendations)
  - [R2 Already-Correct Items](#r2-already-correct-items)
  - [R2 Anti-Patterns and Sign-Off](#r2-anti-patterns-and-sign-off)
  - [R2 Appendix: Evidence and Commands](#r2-appendix-evidence-and-commands)
  - [R2 Receipt Payload Projection](#r2-receipt-payload-projection)
- [Round 3 — Verification](#round-3--verification)
  - [R3 Summary](#r3-summary)
  - [R3 Backlog and Regression Verification](#r3-backlog-and-regression-verification)
  - [R3 Findings](#r3-findings)
  - [R3 Decision and Ask Alignment Verification](#r3-decision-and-ask-alignment-verification)
  - [R3 Resolution Pass](#r3-resolution-pass)
  - [R3 Verification Matrix](#r3-verification-matrix)
  - [R3 Recommendations and Sign-Off](#r3-recommendations-and-sign-off)
  - [R3 Appendix: Files Read and Commands](#r3-appendix-files-read-and-commands)
  - [R3 Receipt Payload Projection](#r3-receipt-payload-projection)

- [Round 4 — Verification](#round-4--verification)
  - [R4 Summary](#r4-summary)
  - [R4.1 Round 1 IC/JA/DV verification](#r41-round-1-icjadv-verification)
  - [R4.2 Round 1 F-N verification](#r42-round-1-f-n-verification)
  - [R4.3 AD-N decisions verification](#r43-ad-n-decisions-verification)
  - [R4.4 NEW issues surfaced](#r44-new-issues-surfaced)
  - [R4 Resolution Pass](#r4-resolution-pass)
  - [R4 Verification Matrix](#r4-verification-matrix)
  - [R4 Recommendations and Sign-Off](#r4-recommendations-and-sign-off)
  - [R4 Appendix: Files Read and Commands](#r4-appendix-files-read-and-commands)
  - [R4 Receipt Payload Projection](#r4-receipt-payload-projection)

## Scope

### Founding ask and alignment

`founding_ask_ref`: `canonical_root_json`: `{"blob_oid":"bee6d633307dfebd3b496531813afbf335f3f9a3","capture_commit":"e869869b929551fb4faa4e9c0584ac6a0a7803cb","content_sha256":"0548b7e69f6e27353a1c56ce9b14c312ed19b3c2a3ddb3689a580640f8d9a9bf","entry_ids":["A001","A002"],"generation":2,"path":"docs/asks/AI-CLI-restart-idle-cc-sessions-safely-nyz8.md","previous_ref_sha256":"50b9450fc8bfb71d4df4706ba470679c9d3b4153f5c3c7af3601dbdd48932d72","repository":"ai-harness","schema_version":1,"task_id":"AI-CLI-restart-idle-cc-sessions-safely-nyz8"}`; `founding_ask_ref_sha256`: `fa12347e1984ca65de5716c748bf5f20e424851b4c54d2468116628648d4d889`.

Full plan audit at commit 2737748b; target SHA-256 a7b78d48f05be58cca6c766510999a1740d03f213d1be51509e4f4b69f62ed43 matches the driver snapshot. Both raw entries were read from the capture commit; blob OID and content SHA-256 match the carrier. This is a specification audit, not implementation certification.

| Authoritative raw entry | Audit coverage |
|---|---|
| A001 | Command forms, idle/context gates, injection, triggers and rollout reviewed. JA-1/JA-3 identify broadened triggers and narrowed verification; DV-1/F-1/F-2 identify prerequisites. Orchestration completion is unverified. |
| A002 | Entire follow-up read, including delegation and feedback delivery. JA-2 escalates the exclusion of requested feedback/issue delivery. |

Local STUB.md/TEMPLATE.md are absent; canonical copies under ~/projects/ai-harness/docs/audits/ supplied the format. The linked research is locally modified (25 insertions, 2 deletions at inspection); its host observations and external citations were not independently certified. No live restart or mobile-visibility test was performed.

<!-- /doc:region name="scope" -->

## Run Ledger

ledger-version: 1

| Field | Value |
|---|---|
| Stage cursor | `doc-audit done: audit-converged; round 4` |
| Promotion | `blocked` |
<!-- doc:region name="loop_receipt" kind="generated" -->
## Status Summary

**Loop state:** audit-converged — NOT promotion ready

| Loop | Round | Target digest | New CRITICAL | New MAJOR | New MINOR | Open blocking | Scope | Stable |
|---|---:|---|---:|---:|---:|---|---|---|
| doc-audit | 4 | sha256:d2fb461881b4268c963dafb0d170131d464169f35ef0b0e7562b77297795e0a4 | 0 | 0 | 0 | — | verify | yes |
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
| 2026-10-17 | 1 | Independent Codex audit: 0 CRITICAL, 6 MAJOR open-blocking, 1 MINOR deferred until live-test preparation; no target edits. |
| 2026-10-17 | 2 | Codex verification: six prior MAJOR and one MINOR fixed in plan; one new MAJOR protocol-deployment gap (N-1); eight reproduced checks; no target edits. |
| 2026-10-17 | 3 | Codex verification: N-1 fixed; F-2, JA-2 and JA-3 reopened after regressions; seven reproduced checks; no target edits. |
| 2026-10-17 | 4 | Codex verification: all three backlog findings fixed in the specification; zero new findings; nine contract checks passed. D-11, implementation and delivery evidence remain pending; no target edits. |

<!-- /doc:region name="audit_log" -->

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

<!-- The reviewer prompt(s) below are the canonical scaffold (kept in sync with
     docs/audits/TEMPLATE.md). Replace the ALL-CAPS placeholders before launching the audit
     agent, and delete the Round 2 appendix if the audit will be single-round. Frozen at
     stub creation. -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** <!-- Opus / Sonnet -->

**Date:** 2026-10-17

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

**Model:** <!-- Opus / Sonnet — ideally a fresh agent for independent verification -->

**Date:** 2026-10-17 (post-Round-1)

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

**Auditor:** Codex; concrete runtime model identifier not exposed.

### R1 Summary

**Not ready:** six MAJOR findings block implementation. One MINOR test-instruction inconsistency is deferred until live-test preparation. No backlog keys were supplied. No fixes or transition authorizations were made. The generated Status Summary remains driver-owned; counts are projected in the receipt below.

CONFIRMED means the cited specification/code gap was reproduced. Runtime consequences are reasoned reachable scenarios, not observed incidents. DV-1's runtime consequence remains PLAUSIBLE pending a disposable-session test.

### R1 Findings

#### Spec / AC Compliance (JA-N)

| ID | Severity | Verdict | Gap |
|---|---|---|---|
| JA-1 | MAJOR | FAIL — CONFIRMED | Ask drift: broadens the automatic trigger to unobserved healthy sessions. |
| JA-2 | MAJOR | FAIL — CONFIRMED | Ask drift: excludes A002 delivery without a re-scope. |
| JA-3 | MAJOR | FAIL — CONFIRMED | Ask drift: narrows restart verification to local registration. |

#### JA-1: Unknown visibility authorizes daily restarts — MAJOR

**Location/evidence:** Plan:522-537 says “the verdict has been `LOCAL_ONLY` for longer than `reachability_max_unverified_hours`” and acknowledges “The age bound restarts a healthy session about once a day when no observer is configured.” Committed A001 instead says “it doesn't necessary  auto-restart unless it detects” the update notice or lost de facto connectivity.

**Impact:** A healthy, current-version session is restarted solely because visibility is unobserved. Idle/context gates and configurability do not supply failure evidence or authorize a broader trigger.

**Verification command:** `rg -n 'reachability_max_unverified_hours|age bound restarts' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

**Recommendation [AUTO-FIX]:** Keep UNKNOWN/LOCAL_ONLY non-triggering; require positive update or reachability-failure evidence. Retaining scheduled healthy-session restarts requires human re-scope. This open-blocking finding escalates the deviation; it grants no authorization.

#### JA-2: A002 delivery is excluded without authority — MAJOR

**Location/evidence:** Plan:45 says “Out of scope for this plan (a separate request)”. Committed A002 requests “do /report-feedback and use SendFeedback”, “also create a github issue as well” and “find any related github issues and add comments linking to our own github issue as appropriate”. Neither raw entry supersedes those instructions.

**Impact:** Plan completion leaves requested feedback/issue delivery unaccounted for. Calling it separate is neither completion evidence nor authorized deferral.

**Verification command:** `rg -n 'A002:' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`; read committed A002 with the command in Appendix: Commands Run.

**Recommendation [AUTO-FIX]:** Add tracked delivery/dependency ACs and completion evidence, or link verified completion elsewhere. Excluding the requirement requires human re-scope. This audit does not send messages or file issues.

#### JA-3: Success does not require peer visibility verification — MAJOR

**Location/evidence:** A001 requires “visible and identifiable via ListAgents tooling under the expected cc session custom title”. Plan AC-2.8 (656-660) ends with “`nameSource` `user`, and a non-null `bridgeSessionId`”. The observer is optional in Configuration/D-8; ListAgents checking is required only during rollout AC-5.3.

**Impact:** An ordinary manual/automatic restart can report success while the original discovery failure persists. Even the research's local probe includes listing/socket checks beyond the registry checks in AC-2.8.

**Verification command:** `sed -n '656,660p' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

**Recommendation [AUTO-FIX]:** Run local listing/socket checks and any configured remote observer after relaunch, matching the replacement identity and expected title. Report separate local, peer and mobile verdicts. Missing observer evidence remains UNVERIFIED; require mobile rollout evidence without claiming host fields prove mobile visibility.

#### Domain Validity (DV-N)

| ID | Severity | Verdict | Gap |
|---|---|---|---|
| DV-1 | MAJOR | FAIL | CONFIRMED missing activation prerequisite; conditional runtime failure PLAUSIBLE. |

#### DV-1: Reconnection depends on an unstated host setting — MAJOR

**Location/evidence:** Plan:104,117,656-660 assumes reconnection. Actual launch commands (session_script.py:1240,1268) use `--resume "$session_id" --name "$ai_name"`; source search finds no `--remote-control` or `remoteControlAtStartup`. Research §5 says only that its test host's “user setting `remoteControlAtStartup` is `true`”. The plan has no activation action or prerequisite gate.

**Impact:** For a session without startup activation or a reusable bridge, the specified restart supplies no way to enable connectivity. Null bridge ID triggers automatic restarts, so such a session can cycle after cooldown without repair. That conditional runtime consequence requires live verification.

**Verification command:** `rg -n -- '--remote-control|remoteControlAtStartup|--resume' src/ai_cli/session_script.py`.

**Recommendation [AUTO-FIX]:** Specify an explicit supported activation/reconnection path or refuse before signalling when prerequisites are absent. Verify the installed CLI interface before choosing arguments. Add a disposable-session test with startup activation disabled and no recorded bridge.

#### F-1: Checking another restarter's lock is not mutual exclusion — MAJOR

**Location/evidence:** Plan:571-577 specifies “A small harness change makes its pass skip a session whose lock is held or whose `last_restart` is inside its own retry window”, then claims “they can never act on one session together.” AC-4.6 likewise requires skipping, not acquiring ownership.

**Impact:** The harness can observe an unlocked session, then the new command acquires its lock before the harness injects /exit. Both act; a second exit can reach the replacement during startup work.

**Verification command:** `sed -n '571,577p' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

**Recommendation [AUTO-FIX]:** Both actors must atomically acquire the same exclusive lock and retain it through revalidation, action and replacement verification. Read/update durable cooldown state under ownership. Test simultaneous attempts with a controlled interleaving.

#### F-2: The supervisor does not pin the selected conversation — MAJOR

**Location/evidence:** Plan:104,117,125-126 promises the same conversation while reusing the supervisor unchanged. main.py:1801-1804 warns on multiple title matches, then executes `matched = candidates[0] if candidates else None`. session_script.py:1227-1242,1263-1271 resolves by title on every launch and falls back to `run_agent claude $claude_perms_flag --name "$ai_name"` without --resume.

**Impact:** Two historical transcripts can share a title while only one registry record is live. After the selected process exits, the supervisor can resume a newer different transcript; missing/mismatched titles can yield a fresh conversation. Post-restart UUID verification detects this only after replacement startup work begins.

**Verification command:** `sed -n '1801,1806p' src/ai_cli/main.py; sed -n '1263,1271p' src/ai_cli/session_script.py`.

**Recommendation [AUTO-FIX]:** Add a pane-generation-fenced supervisor handoff pinning the validated UUID for this restart. Refuse before signalling if its exact resume target cannot be established; forbid title selection/fresh-conversation fallback for controlled restarts. Test duplicate historical titles, title mismatch and missing transcripts. Amend the parity claim to describe this narrow supervisor change.

#### Internal Consistency (IC-N)

| ID | Severity | Verdict | Evidence and recommendation |
|---|---|---|---|
| IC-1 | MINOR | FAIL — CONFIRMED | Plan:139 says “Never restart a process younger than this.” with min_uptime_seconds=1800, but plan:738 requires an “idle and freshly started” session to restart. Wait for minimum uptime or explicitly configure a shorter safe minimum for this test; separately test refusal below the minimum. Deferred until live-test preparation because this does not change the implementation contract. |

**Verification command:** `rg -n 'min_uptime_seconds|idle and freshly' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---|---|---|
| JA-1 | OPEN — MUST fix | Restore trigger boundary; retaining deviation requires human re-scope. |
| JA-2 | OPEN — MUST fix | Account for requested delivery; exclusion requires human re-scope. |
| JA-3 | OPEN — MUST fix | Add post-restart observer checks and partial verdicts. |
| DV-1 | OPEN — MUST fix | Specify activation prerequisites and verify conditional behavior. |
| F-1 | OPEN — MUST fix | Both actors acquire the lock. |
| F-2 | OPEN — MUST fix | Pin UUID before signalling. |
| IC-1 | DEFERRED | Fix test setup before live verification; implementation contract is unaffected. |

No target edits were authorized/applied; OPEN does not mean resolved.

### R1 Verification Matrix

Finding commands were rerun against the unchanged target digest at 2737748b. PASS means the gap reproduces, not that the requirement passes.

| Finding | Command | Expected | Actual | Pass? |
|---|---|---|---|---|
| JA-1 | JA-1 command | Age-only trigger | Lines 148,525,537; “healthy session about once a day” | PASS |
| JA-2 | JA-2 command + committed record read | Requested delivery excluded | Line 45 excludes it; A002 contains feedback/issue/comment requests | PASS |
| JA-3 | JA-3 command | Registration-only success checks | Lines 656-660 have UUID/name/nameSource/bridge ID, no observer check | PASS |
| DV-1 | DV-1 command | No activation argument or prerequisite | Only lines 1227,1240,1268 match; launch has --resume/--name | PASS for gap; runtime untested |
| F-1 | F-1 command | Skip/check instead of shared ownership | Lines 571-573 say skip-if-held; 577 claims no simultaneous action | PASS |
| F-2 | F-2 command | Title-selected UUID/fresh fallback | main.py:1804 selects candidates[0]; script:1270 omits --resume | PASS |
| IC-1 | IC-1 command | Uptime conflict | Lines 139/738: 1800 seconds versus “idle and freshly started” | PASS |

**7/7 specification findings reproduced.** The existing carrier passes `aido validate-doc` (5 regions); the applied relay still needs validation. No suite or live restart test was run. Schema validity does not certify findings or runtime behavior.

## Decisions Requiring Team Input

JA-1/JA-2 are open-blocking ask-drift escalations. Restoring authoritative requirements needs no new design decision. Retaining either deviation requires explicit human re-scope; none is authorized here. No AD-N options or transition authorization are proposed.

## Outstanding Issues to Fix

The six MAJOR findings MUST be fixed before implementation proceeds. IC-1 must be addressed before live verification. Owner: plan author; target: next plan revision. No major deferral is accepted.

## Already-Correct Items

- Paired -X/--restart-session and -Y/--restart-check meet AGENTS.md's option rule (D-6, AC-2.1/2.11).
- Explicit/convention/current-pane forms are covered, and the convention form must not launch a new session (D-2, AC-2.2).
- PID birth identity is required separately from liveness (AC-2.4; process_probe.py:272-285).
- The supervisor keeps its pane on ordinary child exit and has short-run breakers (session_script.py:715-716,1293-1314,1340-1344).
- Avoiding process_probe.end_process is correct for one-PID signalling: it signals a group/tree (process_probe.py:315-327,429-443).
- External injection follows the existing command-drop-in boundary (D-5; session_migrate_hooks.py:64-95).

## Anti-Patterns to Watch For

Do not equate unobserved with unreachable, bridge ID with observer evidence, lock checks with ownership, or detecting a wrong resume afterwards with preventing it. Separate research observations from reproduced facts and schema validation from audit completion.

## Sign-Off Checklist

- [ ] Six MAJOR findings fixed and independently reverified.
- [ ] IC-1 resolved before live verification.
- [ ] A001/A002 accounted for without unauthorized drift.
- [ ] Reconnection/visibility and wrong-title resume cases verified live.
- [x] Finding commands rerun with runtime limits recorded.
- [ ] Relayed document validated; verification round completed.
- [ ] Owner sign-off recorded.

## Appendix: Files Read

**Primary/requirements:** Full plan and audit carrier, AGENTS.md, CLAUDE.md, docs/audits/README.md; complete committed founding record. Canonical ~/projects/ai-harness/docs/audits/STUB.md and TEMPLATE.md (taxonomy, round structure, verification and decision skeleton); design-doc-workflow.md initial guidance.

**Research:** Linked research document, including method, findings, probe, limitations, sources, provenance and Run History. Its host observations/external citations remain unverified.

**Source inspection:** session_script.py (template, heartbeat, signal/relaunch/breaker paths); main.py:408-456,560-750,1797-1822,1938-2010; session_adopt.py:230-282; process_probe.py birth-identity and signal paths, especially 300-332,420-448; session.py:305-360,651-719; config.py:1-130 and symbol search; session_migrate_hooks.py:1-120; stale_session_reaper.py:328-350. All source paths are under src/ai_cli/. Related tests were searched for symbols, not executed. This does not claim exhaustive implementation coverage.

## Appendix: Commands Run

Finding-specific commands are recorded above. Additional grounding/validation commands:

````bash
git status --short
git log -4 --oneline
sha256sum docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
git -C ~/projects/ai-harness show e869869b929551fb4faa4e9c0584ac6a0a7803cb:docs/asks/AI-CLI-restart-idle-cc-sessions-safely-nyz8.md
git -C ~/projects/ai-harness rev-parse e869869b929551fb4faa4e9c0584ac6a0a7803cb:docs/asks/AI-CLI-restart-idle-cc-sessions-safely-nyz8.md
git -C ~/projects/ai-harness show e869869b929551fb4faa4e9c0584ac6a0a7803cb:docs/asks/AI-CLI-restart-idle-cc-sessions-safely-nyz8.md | sha256sum
git diff --stat -- docs/research/claude-code-session-identity-and-reachability.md
rg -n 'resolve-continue-target|read_reaper_config' src tests
rg -n 'start_time_match|capture_identity|procStart|live_sessions' src/ai_cli/process_probe.py src/ai_cli/session_adopt.py
aido validate-doc docs/audits/ai-cli-nyz8-restart-idle-sessions-plan-audit.md
````

### R1 Receipt Payload Projection

````json
{"findings":[{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-broadened"},"disposition":"open-blocking","finding_key":"round1:JA-1","fingerprint":"fp:restart-session:age-only-trigger","id":"JA-1","reachable_behavior":"A healthy current-version session is restarted solely because remote visibility is unobserved.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-dropped"},"disposition":"open-blocking","finding_key":"round1:JA-2","fingerprint":"fp:restart-session:feedback-excluded","id":"JA-2","reachable_behavior":"Completing the plan leaves requested follow-up feedback and issue delivery unaccounted for.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-narrowed"},"disposition":"open-blocking","finding_key":"round1:JA-3","fingerprint":"fp:restart-session:observer-omitted","id":"JA-3","reachable_behavior":"A restart reports success while requested peer visibility can remain broken.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"missing-prerequisite"},"disposition":"open-blocking","finding_key":"round1:DV-1","fingerprint":"fp:restart-session:startup-unrequired","id":"DV-1","reachable_behavior":"A session without bridge startup has no specified reconnection action and may repeatedly fail verification.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"incorrect-behavior"},"disposition":"open-blocking","finding_key":"round1:F-1","fingerprint":"fp:restart-session:lock-check-only","id":"F-1","reachable_behavior":"Two restarters can act because one checks lock status without acquiring ownership.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"incorrect-behavior"},"disposition":"open-blocking","finding_key":"round1:F-2","fingerprint":"fp:restart-session:uuid-unpinned","id":"F-2","reachable_behavior":"The supervisor can resume another same-title transcript or create a fresh conversation before verification fails.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"contradictory-verification"},"disposition":"deferred","finding_key":"round1:IC-1","fingerprint":"fp:restart-session:test-uptime","id":"IC-1","reachable_behavior":"The prescribed freshly started live test is refused by the default minimum uptime gate.","severity":"minor","submitted_severity":"minor"}],"major_nonblocking_justifications":{},"new_by_severity":{"critical":0,"major":6,"minor":1},"scope":{"backlog":[],"diff_since":null,"diff_summary":{"changed":false,"from":null,"to":"sha256:a7b78d48f05be58cca6c766510999a1740d03f213d1be51509e4f4b69f62ed43"},"kind":"full"},"source_event_id":"round-1-audit"}
````

## Round 2 — Verification

**Round 2 auditor:** Codex; concrete runtime model identifier not exposed.

**Round 2 date:** 2026-10-17

**Round 2 scope:** Verify the six driver-supplied blocking findings, the deferred uptime check, and the revised plan against both committed raw ask entries. Append-only verification; no target edits or external delivery actions.

### R2 Summary

Six prior MAJOR findings and the prior MINOR finding are fixed **in the specification**. One new MAJOR deployment prerequisite blocks plan sign-off. This does not certify implementation, reconnection, mobile visibility, feedback delivery or orchestration completion.

The target digest is `9eaff989d3edb98845a1388575ff7a03c9555cd9b579276fe62a2002d5d34f57`, matching the driver's snapshot. HEAD is `7b775650`; the plan revision is in `4331be07`. Only the audit carrier and driver lock files are dirty/untracked at inspection. The linked research is now clean, unlike the Round 1 snapshot. The generated Status Summary remains driver-owned; the receipt below projects this round's findings.

### R2.1 Round 1 IC/JA/DV verification

All plan citations below refer to `docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

| ID | Verdict / disposition | Verified evidence |
|---|---|---|
| DV-1 | PASS / fixed — CONFIRMED | Configuration:181-189 requires effective startup activation, refuses missing/disabled/unsupported/unreadable settings before action, and suppresses automatic failure loops. AC-2.14/2.16 require disabled/enabled activation and retry tests. This resolves the missing prerequisite contract; the supported CLI interface remains an implementation verification obligation. |
| JA-1 | PASS / fixed — CONFIRMED | D-8:568 says “`LOCAL_ONLY` and `UNKNOWN` never trigger, regardless of duration.” The age-only key is removed. AC-4.5 requires observer failure and old-unverified non-action tests. |
| JA-2 | PASS / fixed — CONFIRMED | T-06:827-839 retains report preparation, SendFeedback submission, public issue filing and appropriate related-issue linking comments. “drafting alone shall not satisfy delivery”; blockers prevent epic closure. This restores scope without asserting delivery has happened. |
| JA-3 | PASS / fixed — CONFIRMED | AC-2.8:710-715 requires passive socket/local listing checks and replacement-specific observer checks; missing peer evidence returns non-zero `verification-incomplete`. AC-5.5:813-817 separately requires peer, mobile and SendMessage observations. |
| IC-1 | PASS / fixed — CONFIRMED | Verification:845-850 now waits for `min_uptime_seconds` and separately checks refusal below it; “idle and freshly started” is removed. |

### R2.2 Round 1 F-N verification

| ID | Verdict / disposition | Verified evidence |
|---|---|---|
| F-1 | PASS / fixed — CONFIRMED | D-9:614-623 requires both actors to acquire the same exclusive OS-backed lock, retain it through verification, and update a separate cooldown/result file under ownership. The lock file cannot be replaced/unlinked. AC-2.17 and AC-4.6 require controlled interleavings and block automatic rollout until both actors comply. |
| F-2 | PASS / fixed — CONFIRMED | Configuration:191-203 requires acknowledged generation/old-child identity fencing and exact UUID resume in both launch branches. AC-2.15 forbids title/fresh fallback; AC-2.18 preserves ordinary launches and tests duplicate titles, stale requests and changed generation. N-1 concerns deployment of this new protocol, not whether exact-UUID behavior is now specified. |

### R2.3 AD-N decisions verification

There were no AD-N decisions to apply. All nine target Decisions, including their option bodies and final recommendations, were compared against A001/A002. D-8 now requires positive evidence; T-06 restores the previously excluded A002 work. No new requirement-dropping decision or transition authorization is emitted.

| Authoritative raw entry | Current coverage / limits |
|---|---|
| A001 | Command forms, strict idleness/context checks, shared-library pre-step, positive triggers and visibility checks remain specified. T-05 retains both-host rollout and requires evidence for assignment, handoff, goal activation, integration, synchronization and cleanup. N-1 blocks the newly required protocol's use on existing supervisors. Runtime and coordinator completion remain unverified. |
| A002 | Delegation/goal evidence is required by AC-5.6; all feedback/issue/comment delivery is retained in AC-6.1–6.5. No delivery receipts or completion are claimed by this audit. |

Both complete raw entries were reread from the capture commit in the existing founding-ask carrier. Blob OID and content SHA-256 were independently recomputed and match that carrier. The research's older A002 description still mentions a mobile prompt box (`docs/research/claude-code-session-identity-and-reachability.md`, Founding Ask Coverage); it is stale context, not authority for overriding the corrected T-06 scope.

### R2.4 NEW issues surfaced

#### N-1: Existing supervisors have no specified path into the new handoff protocol — MAJOR

**Location:** Plan Configuration:191-203, AC-2.15:732-737, T-05:803-812; `src/ai_cli/main.py:1389`, `src/ai_cli/main.py:1502`; `src/ai_cli/session_script.py:649`, `src/ai_cli/session_script.py:1157`.

**What the revision specifies:** “Require an acknowledgement from that same live supervisor generation before action. Supervisors without handoff support refuse with `resume-target`”. Rollout AC-5.1 installs the new package/harness, then AC-5.2 runs `ai -X` against existing live sessions. Its live test instead starts a new disposable session (Verification:845).

**Actual state — CONFIRMED specification/deployment gap:** The existing update path atomically replaces the script on disk: `temporary.replace(script_path)` (`main.py:1397`). The running shell remains on the old inode; `_refresh_live_session_scripts` refreshes artifacts for the “next restart” (`main.py:1506`). The persistent supervisor waits for its child and launches replaceable child bodies (`session_script.py:649-740`); child template reload is reached after the current Claude process exits (`session_script.py:1157-1172`). No existing supervisor acknowledgement implementation was found in that loop. The full revised plan specifies refusal for old supervisors but no compatibility adapter, supervisor upgrade procedure, or pre-feature-supervisor deployment test. Updating artifacts does not itself equip the running supervisor to acknowledge the request.

**Reachable consequence — PLAUSIBLE, not a live incident:** An otherwise eligible long-running session created before this feature refuses `resume-target` after installation. The restart needed to load new child behavior is gated by acknowledgement that its existing supervisor cannot provide under the specified protocol. Newly created disposable sessions can pass while the sessions that motivated rollout remain unrestarted. A runtime-loaded compatibility adapter could resolve this, but the plan does not specify one or its safety proof.

**Verification command:**

````bash
rg -n 'old inode|temporary.replace|next restart' src/ai_cli/main.py
rg -n 'Require an acknowledgement|without handoff|AC-5.1|AC-5.2' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
sed -n '649,740p' src/ai_cli/session_script.py
sed -n '1157,1172p' src/ai_cli/session_script.py
````

**Actual output:** `main.py:1392` says “running shell on the old inode; the next exec opens the new one”; line 1397 replaces the file; line 1506 defers hot reload until the next restart. Plan lines 193-194 require acknowledgement/refusal, while lines 803-805 install and immediately invoke the command. The inspected loop contains child launch/wait/reload handling, with no acknowledgement route.

**Recommended fix [AUTO-FIX]:** Specify how an existing supervisor gains verified protocol support before its first controlled restart. Identify the executing component that acknowledges the request and prove the next child will consume only its pinned UUID; keep refusal when that cannot be established. Add a rollout prerequisite and regression test that starts the pre-feature supervisor, updates package/scripts while its Claude child remains live, then completes a safe pinned-UUID restart in the same pane. Do not waive the handshake or substitute an unverified exit to bootstrap it.

### R2 Resolution Pass

| Item | Status | Required action |
|---|---|---|
| Prior six blocking findings | Fixed in plan | Implementation must satisfy the new ACs and negative-path tests. |
| Prior uptime inconsistency | Fixed in plan | Execute the corrected live setup during implementation verification. |
| N-1 | OPEN — MUST fix before plan sign-off | Plan author specifies and tests the existing-supervisor upgrade path. |

No target or source changes were made by this audit. No AD-N team decision is necessary to state the missing deployment contract.

### R2 Verification Matrix

Each listed command was actually run against the matched target digest. PASS means the quoted contract is present, or that the N-1 gap is reproduced; it does not mean runtime acceptance passed. In this table, `P` abbreviates `docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

| Finding | Command | Expected | Actual | Result |
|---|---|---|---|---|
| DV-1 | `sed -n '181,189p' P` | Activation refusal and failure suppression | `rc-activation` before action; automatic attempts suppressed until probe/manual retry | PASS |
| F-1 | `sed -n '614,623p' P` | Ownership by both actors | Exclusive OS-backed lock, stable lock file and separate durable cooldown under ownership | PASS |
| F-2 | `sed -n '191,203p' P` | Pinned exact conversation | Same-generation acknowledgement, old-child fencing, exact UUID in either branch; no fallback | PASS |
| JA-1 | `sed -n '561,571p' P` | Positive triggers only | Unknown versions/access remain unknown; local-only/unknown never trigger at any age | PASS |
| JA-2 | `sed -n '822,839p' P` | Retained delivery | All report/submission/issue/comment ACs and closure blocker present | PASS |
| JA-3 | `sed -n '706,715p' P` | Independent observer verification | Local socket/listing and remote observer; missing peer evidence is non-zero incomplete | PASS |
| IC-1 | `sed -n '845,850p' P` | Safe live-test uptime | Wait for minimum, test refusal below it, then cooldown refusal | PASS |
| N-1 | N-1 commands above | Artifact refresh is insufficient for live acknowledgement | Atomic replacement/next-restart reload versus mandatory pre-action acknowledgement; no migration specified | PASS for gap; runtime untested |

**8/8 checks reproduced.** A separate in-memory assertion pass reported seven PASS results for the prior findings. `aido validate-doc` passes for the current plan and existing audit carrier, each with five regions. The relayed additions still require validation after application. No pytest suite or live restart test ran; those would not establish a documentation-only contract's implementation here.

### R2 Recommendations

**MUST be fixed before plan sign-off:** N-1; owner: plan author; target: next plan revision. The six prior MAJOR findings do not remain open merely because implementation has not begun.

**SHOULD be fixed before delivery preparation:** Correct the linked research's stale A002 summary so report preparation uses the Remote Control discovery failure specified by T-06.

**Outstanding implementation/delivery gates:** Activation interface verification, controlled concurrency, pinned-UUID resume, peer/mobile observations, SendMessage delivery and feedback receipts remain required by the plan. They are not completion claims of this doc-audit round.

### R2 Already-Correct Items

- Positive evidence replaces the unverified-age restart trigger (D-8:561-571).
- Both restarters acquire ownership through completion/failure (D-9:614-623).
- Exact UUID resume covers prompt-present and prompt-absent launches (Configuration:198-200; AC-2.15).
- Local, peer and mobile verification are separate, with incomplete peer evidence failing verification (AC-2.8; AC-5.5).
- Feedback drafting cannot substitute for delivery; related-issue review and closure blockers are explicit (AC-6.1–6.5).
- Minimum uptime now has both a valid live setup and a negative check (Verification:845-850).

### R2 Anti-Patterns and Sign-Off

Do not infer that replacing a script upgrades a shell already executing its old inode. A fresh disposable session tests the new protocol but cannot prove migration from deployed supervisors. Preserve the distinction between fixed specification gaps and unexecuted implementation obligations.

- [x] All six blocking backlog items and the deferred minor checked against the current plan.
- [x] Both complete raw ask entries reread; revised Decisions compared against them.
- [x] Eight verification checks executed; schema validation limits recorded.
- [ ] N-1 fixed and independently reverified.
- [ ] Relayed audit additions validated after application.
- [ ] Owner sign-off recorded.

### R2 Appendix: Evidence and Commands

**Read:** Full revised plan including empty Feedback Rounds/Approval Log, full audit history, both committed raw ask entries, linked research, AGENTS.md, CLAUDE.md and audit README. Canonical audit STUB/TEMPLATE were read from `~/projects/ai-harness/docs/audits/` because local copies remain absent; design-doc-workflow guidance was consulted.

**Source/test inspection:** `src/ai_cli/session_script.py` heartbeat, persistent supervisor, child reload, both launch branches and breakers; `src/ai_cli/main.py` atomic script writer, script refresh/update, title resolver and heartbeat handler; registry reader in `session_adopt.py`, birth-identity methods in `process_probe.py`, config/path setup in `config.py`, naming/slot functions in `session.py`, and `session_migrate_hooks.py`. Read `tests/test_session_script_atomic_write.py`, whose old-program-versus-refreshed-script regression confirms the relevant intended behavior; the test was not executed. This is source inspection, not exhaustive implementation coverage.

**Existing out-of-scope standards issue:** `src/ai_cli/config.py:119` hardcodes a private harness name in the machine-marker path, conflicting with the repository's public-package naming rule. Flagged during inspection; it is not introduced by this plan revision and is not a new blocking finding in this verification scope.

Additional commands actually run: `git status --short`; `git log -4 --oneline`; `git diff 2737748b HEAD -- docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`; `sha256sum docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`; committed founding-record `git show`, `git rev-parse` and SHA-256 commands from Round 1; both `aido validate-doc` commands. Symbol searches and subsequent line reads checked supervisor handoff, heartbeat, registry, naming and configuration claims. No processes were signalled, messages sent or files written.

### R2 Receipt Payload Projection

````json
{"findings":[{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-broadened"},"disposition":"fixed","finding_key":"round1:JA-1","fingerprint":"fp:restart-session:age-only-trigger","id":"JA-1","reachable_behavior":"A healthy current-version session is restarted solely because remote visibility is unobserved.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-dropped"},"disposition":"fixed","finding_key":"round1:JA-2","fingerprint":"fp:restart-session:feedback-excluded","id":"JA-2","reachable_behavior":"Completing the plan leaves requested follow-up feedback and issue delivery unaccounted for.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-narrowed"},"disposition":"fixed","finding_key":"round1:JA-3","fingerprint":"fp:restart-session:observer-omitted","id":"JA-3","reachable_behavior":"A restart reports success while requested peer visibility can remain broken.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"missing-prerequisite"},"disposition":"fixed","finding_key":"round1:DV-1","fingerprint":"fp:restart-session:startup-unrequired","id":"DV-1","reachable_behavior":"A session without bridge startup has no specified reconnection action and may repeatedly fail verification.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"incorrect-behavior"},"disposition":"fixed","finding_key":"round1:F-1","fingerprint":"fp:restart-session:lock-check-only","id":"F-1","reachable_behavior":"Two restarters can act because one checks lock status without acquiring ownership.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"incorrect-behavior"},"disposition":"fixed","finding_key":"round1:F-2","fingerprint":"fp:restart-session:uuid-unpinned","id":"F-2","reachable_behavior":"The supervisor can resume another same-title transcript or create a fresh conversation before verification fails.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"contradictory-verification"},"disposition":"fixed","finding_key":"round1:IC-1","fingerprint":"fp:restart-session:test-uptime","id":"IC-1","reachable_behavior":"The prescribed freshly started live test is refused by the default minimum uptime gate.","severity":"minor","submitted_severity":"minor"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@4331be07","subsystem":"session-restart","violation_kind":"missing-prerequisite"},"disposition":"open-blocking","finding_key":"round2:N-1","fingerprint":"fp:restart-session:existing-supervisor-bootstrap","id":"N-1","reachable_behavior":"A live session running the pre-feature supervisor refuses the newly mandatory acknowledgement after package installation because no compatible upgrade path is specified.","severity":"major","submitted_severity":"major"}],"major_nonblocking_justifications":{},"new_by_severity":{"critical":0,"major":1,"minor":0},"scope":{"backlog":["round1:DV-1","round1:F-1","round1:F-2","round1:JA-1","round1:JA-2","round1:JA-3"],"diff_since":"sha256:a7b78d48f05be58cca6c766510999a1740d03f213d1be51509e4f4b69f62ed43","diff_summary":{"changed":true,"from":"sha256:a7b78d48f05be58cca6c766510999a1740d03f213d1be51509e4f4b69f62ed43","to":"sha256:9eaff989d3edb98845a1388575ff7a03c9555cd9b579276fe62a2002d5d34f57"},"kind":"verify"},"source_event_id":"round-2-audit"}
````

## Round 3 — Verification

**Round 3 auditor:** Codex; concrete runtime model identifier not exposed.

**Date:** 2026-10-17

**Scope:** Verify the supplied N-1 backlog and changes since Round 2, including regressions in previously fixed findings. No target edits.

### R3 Summary

N-1 is fixed by removing the acknowledgement prerequisite. Three earlier MAJOR findings regress: F-2, JA-2 and JA-3 are reopened under their original IDs and fingerprints. No new finding IDs are needed. Plan sign-off remains blocked.

HEAD is `8c536da8`. The target SHA-256 is `bf41ec9c54129e10a069565e468702b4046ca89e558bea023bf0b5dbe1b252fb`, matching the driver. The plan is clean; the audit carrier, receipt and driver locks are modified/untracked. Both committed raw ask entries were reread; their blob OID and content digest match the supplied carrier. Local audit STUB/TEMPLATE files remain absent; canonical copies in `~/projects/ai-harness/docs/audits/` supplied the format. This verifies a plan, not implemented or live behavior.

### R3 Backlog and Regression Verification

All plan citations refer to `docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

| ID | Disposition / verdict | Evidence |
|---|---|---|
| N-1 | fixed / PASS | D-10:654-666 and AC-2.15 replace mandatory supervisor acknowledgement with a pre-action candidate check. Old supervisors need no new acknowledgement implementation. This removes the specific bootstrap dependency; F-2 below identifies the replacement's safety regression. |
| F-2 | open-blocking / FAIL | D-10:670-671 explicitly permits a different same-title transcript to win between check and relaunch. |
| JA-2 | open-blocking / FAIL | Coverage:45 replaces T-06 with an unsupported delivery claim; the complete task breakdown contains no feedback-delivery ACs. |
| JA-3 | open-blocking / FAIL | Configuration:168-171 allows local success without an observer; AC-2.8 omits the previous incomplete-peer exit contract, contrary to AC-5.2:902. D-11:747 ships the fallback despite its pending human decision. |

### R3 Findings

#### F-2: The replacement for the handoff reintroduces wrong-conversation resume — MAJOR

**Evidence — CONFIRMED:** D-10:670-671 states, verbatim, “A same-title transcript written in the seconds between the check and the relaunch would still be resumed instead.” D-10:690-693 relies on “only the session itself writes transcripts under its title” and detection after relaunch. Source `src/ai_cli/main.py:408-436` searches mutable transcript titles and sorts by mtime; `main.py:1804` selects `candidates[0]`. Both launch branches in `src/ai_cli/session_script.py:1235-1271` resolve again and retain a fresh-conversation fallback. The resolver docstring itself documents inherited titles on forked transcripts, so title ownership is not an established exclusivity guarantee.

**Impact:** A pre-check of one candidate does not bind the later launch to that UUID. Another interactive conversation created or renamed to the same title during the interval can replace it; reporting `resume-mismatch` afterwards does not prevent running the wrong conversation. This is a reachable interleaving demonstrated with the actual resolver function and mocked transcript inputs, not a claimed live incident.

**Verification:**

````bash
sed -n '654,693p' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
sed -n '408,436p' src/ai_cli/main.py
sed -n '1797,1818p' src/ai_cli/main.py
sed -n '1230,1273p' src/ai_cli/session_script.py
````

**Recommended fix [AUTO-FIX]:** Preserve exact-conversation selection through launch, with refusal rather than title/fresh fallback when that cannot be guaranteed. Specify and test a compatible path for existing supervisors instead of resolving N-1 by removing this protection. Add an interleaving test that introduces a newer same-title transcript after the final pre-check and proves it cannot become the replacement.

#### JA-2: Requested feedback delivery is removed again without matching completion evidence — MAJOR

**Evidence — CONFIRMED:** A002 explicitly requests “use SendFeedback”, “create a github issue” and “add comments linking to our own github issue as appropriate”. Coverage:45 now says “Owned and delivered by the launching session, not by this plan” and cites no receipt or report path. The revision deletes T-06 and its delivery/closure ACs. The discoverable current report, `~/projects/ai-harness/docs/feedback/claude-ios-code-tab-long-prompt-unnavigable.md:31-34`, concerns prompt-box scrolling and text truncation. Its Submission Tracking:128 records locally queued SendFeedback and issue #103074 for that different defect. The research coverage table also describes A002 as prompt-box work; that summary cannot override the raw ask's Remote Control context. An older reachability report was found too; neither its existence nor the unrelated current report proves this ask's delivery.

**Impact:** The plan can close while the requested Remote Control/discovery report, issue and related-issue review remain unaccounted for. Moving work to another owner is acceptable when the exact work and completion evidence remain traceable; the current completion assertion does not supply that evidence. This finding does not assert that no report exists anywhere.

**Verification:**

````bash
rg -n 'Owned and delivered|T-06|AC-6\.' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
sed -n '29,35p;123,132p' ~/projects/ai-harness/docs/feedback/claude-ios-code-tab-long-prompt-unnavigable.md
rg -n 'reachability|Submission Tracking|submitted' ~/projects/ai-harness/docs/feedback/remote-control-stale-pairings-truncate-reachability.md
````

**Recommended fix [AUTO-FIX]:** Restore tracked delivery and an epic-closure gate, or link the actual Remote Control report, SendFeedback outcome, filed issue and related-issue review/comments from the launching session. Keep incomplete routes pending and distinguish queued feedback from confirmed submission. Any exclusion of A002 requires human re-scope; no such authorization appears in the committed record or empty Approval Log.

#### JA-3: Peer verification and the unresolved visibility requirement become optional again — MAJOR

**Evidence — CONFIRMED:** A001 requires checks that the session is “truly” connected and “visible and identifiable via ListAgents tooling under the expected cc session custom title”. Configuration:169-171 says that without an observer the restart “still succeeds on its local and bridge checks”. AC-2.8:812-815 requires an observer only where configured and explicitly fails only local/bridge checks. AC-5.2:902 still requires “Unverified peer evidence is a non-zero incomplete result, not reachable success.” AC-5.5 no longer requires the prior independent SendMessage observation. D-11 is marked pending human at line 699, but line 747 says “The build ships (a), which D-8 already implements, so nothing waits on this choice.”

**Impact:** An implementation can report success while the original peer-discovery failure remains unobserved, and rollout has a contradictory exit contract. Naming a blind spot is honest reporting but does not authorize shipping a narrowed requirement while its human escalation is unresolved. A configured cross-host listing also cannot by itself establish mobile visibility or message delivery.

**Verification:**

````bash
sed -n '168,181p;699,750p;807,815p;899,912p' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
rg -n 'SendMessage|observer|UNVERIFIED' docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md
````

**Recommended fix [AUTO-FIX]:** Restore a single incomplete-peer/non-zero contract for the command and rollout, and retain independent peer/message observations. Keep mobile evidence separate. Make the unresolved D-11 narrowing block completion of that requirement until the owner explicitly re-scopes it or evidence satisfies it; do not replace unknown status with an age-only restart trigger.

### R3 Decision and Ask Alignment Verification

No audit AD-N decision was previously selected. Target D-10(a) removes the deployment dependency but regresses F-2. Target D-11 is a human escalation whose proposed fallback must remain pending; its shipment clause is covered by JA-3. Restoring requirements requires no new audit decision, and this round authorizes no transition.

| Raw entry | Current coverage and limits |
|---|---|
| A001 | Command forms, idle/context gates, injection pre-step, positive triggers and both-host rollout remain specified. F-2 and JA-3 identify reopened safety/visibility gaps. Coverage:44 asserts task assignment and goal activation without attached execution proof; those orchestration claims remain UNVERIFIED. The linked run prompt is a brief, not proof of execution. |
| A002 | Full follow-up reread. JA-2 identifies removed delivery tracking. No report, issue, message, merge, goal activation or cleanup was performed by this audit. |

### R3 Resolution Pass

| Item | Result | Owner / next action |
|---|---|---|
| N-1 | Fixed in specification | Preserve compatibility while addressing F-2. |
| F-2 | Reopened; MUST fix | Plan author: bind the launch to the selected conversation and test the interleaving. |
| JA-2 | Reopened; MUST fix | Plan author/coordinator: restore tracked delivery or attach matching receipts. |
| JA-3 | Reopened; MUST fix | Plan author: restore incomplete verification semantics and retain the pending human escalation as a completion gate. |

No inline fixes or major deferrals were applied. The original finding IDs and fingerprints are retained because these are regressions of the same invariants, not independent new findings.

### R3 Verification Matrix

Every command below was run. PASS means the stated observation reproduced, not that runtime acceptance passed. `P` denotes the plan path above.

| Check | Command / method | Actual output or observation | Result |
|---|---|---|---|
| Snapshot | `sha256sum P`; committed ask `git rev-parse` and `git show ... \| sha256sum` | Plan `bf41ec9c...b252fb`; ask blob `bee6d633...`; ask SHA-256 `0548b7e6...8d9a9bf`, all matching | PASS |
| N-1 | `rg -n 'supervisor-acknowledged\|require exactly one' P` plus D-10 read | Candidate check at 658; acknowledgement only in unchosen option (b), 673 | PASS: old prerequisite removed |
| F-2 contract | `sed -n '654,693p' P`; resolver/launch reads above | Check/relaunch gap expressly accepted at 670; second resolver still selects newest candidate | PASS: regression reproduced |
| F-2 interleaving | In-memory AST extraction of repository `_find_cc_session_candidates_by_title`, with mocked project/transcript objects | `precheck: ['original']`; after adding a newer same-title interactive transcript, `relaunch: ['replacement', 'original']` | PASS: actual resolver behavior, simulated inputs |
| JA-2 | `rg -n 'Owned and delivered\|T-06\|AC-6\.' P`; report reads above | Only coverage line 45 matches; current report describes prompt scrolling and records queued feedback at 128 | PASS: tracking removal reproduced |
| JA-3 | `rg -n 'nothing waits\|still succeeds\|Unverified peer evidence' P` plus AC-2.8/5.5 reads | Success at 170; ships pending choice at 747; non-zero incomplete at 902 | PASS: contradictory/narrowed contract reproduced |
| Retained lock fix | `rg -n 'OS-backed lock\|retain ownership\|until both actors' P` | Ownership at 606/608; rollout blocked until both actors comply at 893 | PASS |

**7/7 checks reproduced.** The interleaving check executed the repository function with fake metadata and made no filesystem changes. No live restart, process signal, network delivery or pytest suite ran. `aido validate-doc` passed for both the current plan and current carrier, each with five regions; the final relayed additions still require validation after application. Region validation does not establish semantic correctness.

### R3 Recommendations and Sign-Off

**MUST before plan sign-off:** Resolve F-2, JA-2 and JA-3. Owner: plan author/coordinator; target: next plan revision. No additional implementation work is authorized by this review.

**Already correct:** The default 20% configurable context gate remains (D-4); the strict background-work gate now requires an explicit zero pending-agent count (D-3); unknown/local-only evidence never triggers by age (D-8); shared lock ownership remains required (D-9); the live test still waits for minimum uptime (Verification).

**Anti-pattern:** Closing a deployment finding by deleting its safety prerequisite can reopen an earlier finding. Likewise, an outside owner's completion assertion and an unrelated submitted issue cannot replace requirement-specific receipts. Proposed procedure improvement: require a regression check of every previously fixed invariant touched by a compatibility fix.

- [x] Supplied backlog item independently checked and disposition recorded.
- [x] Both raw ask entries and changed Decisions compared with current plan.
- [x] Verification commands executed; observed facts separated from live-system limits.
- [ ] Three reopened MAJOR findings resolved and independently reverified.
- [ ] Pending human re-scope resolved before claiming the affected requirement complete.
- [ ] Relayed additions validated and owner sign-off recorded.

### R3 Appendix: Files Read and Commands

**Documents:** Entire current plan and prior audit history; both complete raw entries from capture commit `e869869b929551fb4faa4e9c0584ac6a0a7803cb`; local AGENTS.md, CLAUDE.md and audit README; canonical audit STUB/TEMPLATE; linked research. Read the current prompt-box feedback report in full and the older reachability report's matching sections. Inspected the linked coordinator prompt's founding carrier and task/output guidance; it is not an execution receipt. Plan Feedback Rounds and Approval Log are empty.

**Code/tests:** `src/ai_cli/main.py:408-560,1380-1400,1797-1818`; `src/ai_cli/session_script.py:649-740,1148-1176,1220-1295`; full `tests/test_session_script_atomic_write.py`. These reads verify resolver, live-owner, reload and launch semantics; they do not constitute an implementation audit.

**Commands:** Matrix and finding commands above; `git status --short`; `git log -5 --oneline`; `git diff 4331be07 8c536da8 -- docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`; full document reads; feedback-directory symbol searches and filename searches across the harness docs; `aido validate-doc` for the plan and audit carrier. The resolver experiment used `python3 -B`, AST extraction and an in-memory `Project` with two fake `Transcript` records; no scratch files were created. The existing public-package naming violation recorded in Round 2 remains outside this diff's scope.

### R3 Receipt Payload Projection

````json
{"findings":[{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@4331be07","subsystem":"session-restart","violation_kind":"missing-prerequisite"},"disposition":"fixed","finding_key":"round2:N-1","fingerprint":"fp:restart-session:existing-supervisor-bootstrap","id":"N-1","reachable_behavior":"A live session running the pre-feature supervisor refuses the newly mandatory acknowledgement after package installation because no compatible upgrade path is specified.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"incorrect-behavior"},"disposition":"open-blocking","finding_key":"round1:F-2","fingerprint":"fp:restart-session:uuid-unpinned","id":"F-2","reachable_behavior":"The supervisor can resume another same-title transcript or create a fresh conversation before verification fails.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-dropped"},"disposition":"open-blocking","finding_key":"round1:JA-2","fingerprint":"fp:restart-session:feedback-excluded","id":"JA-2","reachable_behavior":"Completing the plan leaves requested follow-up feedback and issue delivery unaccounted for.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-narrowed"},"disposition":"open-blocking","finding_key":"round1:JA-3","fingerprint":"fp:restart-session:observer-omitted","id":"JA-3","reachable_behavior":"A restart reports success while requested peer visibility can remain broken.","severity":"major","submitted_severity":"major"}],"major_nonblocking_justifications":{},"new_by_severity":{"critical":0,"major":0,"minor":0},"scope":{"backlog":["round2:N-1"],"diff_since":"sha256:9eaff989d3edb98845a1388575ff7a03c9555cd9b579276fe62a2002d5d34f57","diff_summary":{"changed":true,"from":"sha256:9eaff989d3edb98845a1388575ff7a03c9555cd9b579276fe62a2002d5d34f57","to":"sha256:bf41ec9c54129e10a069565e468702b4046ca89e558bea023bf0b5dbe1b252fb"},"kind":"verify"},"source_event_id":"round-3-audit"}
````

## Round 4 — Verification

**Round 4 auditor:** Codex; concrete runtime model identifier not exposed.

**Date:** 2026-10-17

**Scope:** Verify the three supplied backlog findings and regressions introduced by their fixes. Append-only verification; the plan and source remain unchanged.

### R4 Summary

All three backlog findings are **fixed in the specification**. No new CRITICAL, MAJOR or MINOR findings were established. This does not certify implementation, host compatibility, feedback delivery or reachability. D-11 remains pending and explicitly blocks visibility completion, plan sign-off and epic closure.

HEAD is `8b9b5877`. The plan SHA-256 is `d2fb461881b4268c963dafb0d170131d464169f35ef0b0e7562b77297795e0a4`, matching the driver. The plan is clean; the audit carrier is modified and two driver lock files are untracked. Both complete raw entries were reread and checked against capture commit `e869869b929551fb4faa4e9c0584ac6a0a7803cb`: blob `bee6d633307dfebd3b496531813afbf335f3f9a3`, SHA-256 `0548b7e69f6e27353a1c56ce9b14c312ed19b3c2a3ddb3689a580640f8d9a9bf`; the resolved working copy matches those committed bytes. Local audit STUB/TEMPLATE remain absent; canonical copies under `~/projects/ai-harness/docs/audits/` supplied the format.

All plan citations below refer to `docs/plans/ai-cli-nyz8-restart-idle-sessions-plan.md`.

### R4.1 Round 1 IC/JA/DV verification

| ID | Disposition / verdict | Evidence and verification note |
|---|---|---|
| JA-2 | fixed / PASS — CONFIRMED | T-06:960-980 restores the correct Remote Control report, SendFeedback submission, filed issue and related-issue review/comment receipts. AC-6.2 says “locally queued feedback remains pending until confirmed, not delivered”; AC-6.5 blocks epic closure and requires explicit operator re-scope to drop a route. Coverage:45 no longer asserts outside-owner delivery. The delivery-contract assertion function passed. Actual submissions remain unverified, as the plan now states. |
| JA-3 | fixed / PASS — CONFIRMED | Configuration:170-174 and AC-2.8:838-844 require non-zero `verification-incomplete` for missing/unknown peer evidence. AC-5.2:938 repeats that result; AC-5.5:943-950 requires independent SendMessage receipt/reply and mobile title/connection observations. D-11:768-774 says “D-11 blocks completion of the visibility requirement, plan sign-off and epic closure”. The visibility-contract function and peer/escalation assertions passed; local registration no longer substitutes for the missing surfaces. |

### R4.2 Round 1 F-N verification

| ID | Disposition / verdict | Evidence and verification note |
|---|---|---|
| F-2 | fixed / PASS — CONFIRMED | D-10:661-680 requires compatible-child preparation before action and a durable exact-UUID request checked before title resolution or prompt consumption. It states “No title lookup or fresh-conversation fallback is permitted for a requested restart” and “Cooldown expiry cannot clear it.” AC-2.15:860-866 and AC-2.18:874-878 require both launch branches, failure fencing and a newer same-title competitor test. AC-5.7:953-958 requires the first restart from a pre-feature supervisor without replacing the pane/supervisor. Resume-contract and pin/compatibility assertions passed. |

The compatibility route is grounded in existing source: `src/ai_cli/session_script.py:649-661` opens the stable child path on each supervisor iteration; lines 1162-1169 detect a replaced script and request child reload; lines 1379-1381 return to the persistent supervisor after the agent exits. `src/ai_cli/main.py:1389-1397` uses atomic replacement, preserving the executing inode. These facts support the proposed route, but do not prove compatibility for any running version. The plan now requires that proof and refuses unknown versions. Current title-based launches at `session_script.py:1235-1271` still need the specified implementation change; their present behavior is not evidence that the revised plan is implemented.

### R4.3 AD-N decisions verification

No audit AD-N choice was selected or resolved in this round. Target D-10 specifies the safety correction and compatibility refusal; D-11 remains an operator decision. Its recommendation is explicitly not permission to ship narrowed visibility. No transition or re-scope is authorized by this audit.

| Authoritative raw entry | Coverage and remaining limits |
|---|---|
| A001 | Command forms, strict idleness, configurable context gate, shared injection, positive triggers, both-host rollout and merge/sync/reap evidence remain specified. The exact-conversation contract is restored. Coverage:44 now labels assignment/handoff/delegation/goal execution unverified pending receipts. Peer/mobile completion remains gated by D-11; no execution or live visibility is inferred. |
| A002 | T-06 retains all requested reporting routes and appropriate related-issue comments with route-specific receipts and a closure gate. Delegation and goal activation remain explicitly unverified. No delivery completion is claimed by this audit. |

### R4.4 NEW issues surfaced

None established in this verification scope. The unresolved D-11 choice and unexecuted implementation/delivery ACs remain explicit gates rather than newly discovered omissions.

### R4 Resolution Pass

| Item | Result | Remaining obligation |
|---|---|---|
| F-2 | Fixed in plan | Implement the pin and failure fence; execute AC-2.18 and the pre-feature compatibility test AC-5.7. |
| JA-2 | Fixed in plan | Launching owner supplies the matching report, submission, issue and related-issue receipts before closure. |
| JA-3 | Fixed in plan | Implement incomplete-peer results; obtain peer/message/mobile evidence and resolve D-11 before claiming completion. |

No inline fixes, new audit decisions or major deferrals were applied. Stable original IDs and fingerprints are retained in the projection.

### R4 Verification Matrix

Commands were executed against the matched plan digest. PASS here verifies specification content, not runtime behavior. `P` denotes the plan path above. The Python check used `python3 -B`, parsed `tests/test_restart_plan_contract.py` with `ast`, compiled its undecorated assertion functions in memory, and called each with the current plan text; it did not run pytest or its fixtures.

| Check | Command / method | Actual output / observation | Result |
|---|---|---|---|
| F-2 regression guard | Execute the existing resume-contract assertion function in memory | `PASS: test_given_restart_plan_when_resume_contract_checked_then_pin_and_existing_session_upgrade_are_required` | PASS |
| JA-2 regression guard | Execute the existing delivery-contract assertion function in memory | `PASS: test_given_restart_plan_when_delivery_contract_checked_then_specific_receipts_gate_closure` | PASS |
| JA-3 regression guard | Execute the existing visibility-contract assertion function in memory | `PASS: test_given_restart_plan_when_visibility_contract_checked_then_incomplete_evidence_cannot_pass` | PASS |
| Pin and deployment details | Assert no fallback, retained fence, old-child identity, pre-feature supervisor and unknown-version refusal in P; read D-10/source paths above | `PASS: pin-failure-fence`; `PASS: compatible-upgrade`; supervisor opens stable child and old child returns to it | PASS |
| Peer results and delivery details | Assert non-zero incomplete result, correlated evidence, queued-feedback pending and epic-closure gate in P | `PASS: peer-result`; `PASS: delivery-closure` | PASS |
| Escalation and retained safety | Assert D-11 blocker, no age-only restart, message reply, shared lock, two-actor rollout blocker and activation refusal in P | `PASS: visibility-escalation`; `PASS: lock-and-activation-regression` | PASS |
| Mechanical fidelity floor | `python3 -B ~/projects/ai-harness/scripts/far_fidelity.py check --doc P` | `0 finding(s)`; tool also says `founding ask: not read (no AI-resolved decision states a negation)` | PASS for mechanical check only; raw-ask comparison performed separately |
| Region validation | `aido validate-doc P`; same command for the current audit carrier | Both `OK`, correct template versions, five regions each | PASS for pre-relay files only |

**9/9 contract checks passed**, covering all three backlog findings plus six detailed/regression assertions. Snapshot hashes also matched. The assertions establish text coverage; they do not exercise a signal, lock race, supervisor migration or peer observer. Region validation does not check semantic completeness. The relayed additions require validation after application.

### R4 Recommendations and Sign-Off

No additional specification fix is required by the three supplied backlog findings. Keep the implementation and rollout gates intact. D-11 still blocks plan sign-off until supporting evidence or an explicit operator re-scope is recorded; a clear doc-audit backlog alone does not close it.

**Already correct:** Exact-UUID selection covers prompt and no-prompt branches with retained failure fencing; feedback receipts are tracked for the correct defect; incomplete peer checks cannot report reachable success; independent message/mobile observations and D-11 remain required. Each is cited and reproduced above.

**Anti-pattern:** A text regression test can pass without any runtime feature implementation. Likewise, an atomic script writer supports a migration design but does not certify deployed supervisors. Keep specification resolution separate from implementation acceptance and delivery receipts.

- [x] Every supplied backlog finding verified with its stable identity and current disposition.
- [x] Both committed raw ask entries compared with changed decisions and coverage rows.
- [x] Nine contract checks and the mechanical/region checks executed; limits recorded.
- [ ] Exact-UUID interleaving and pre-feature supervisor tests executed against the implementation.
- [ ] D-11 resolved and required peer/message/mobile evidence recorded.
- [ ] Feedback, orchestration and delivery receipts verified.
- [ ] Relayed audit additions validated and owner sign-off recorded.

### R4 Appendix: Files Read and Commands

**Documents:** Full current plan, complete prior audit history, full linked research, both complete committed raw ask entries and their resolved working copy, AGENTS.md, CLAUDE.md and audit README. Canonical audit STUB/TEMPLATE and the design-doc workflow were read under `~/projects/ai-harness/docs/`. Research's A002 coverage now identifies Remote Control/discovery and T-06 correctly. External citations and host observations were not independently re-certified.

**Code/tests:** `src/ai_cli/main.py:380-440,1380-1405,1790-1825`; `src/ai_cli/session_script.py:649-745,1145-1180,1215-1386`; full `tests/test_session_script_atomic_write.py` and `tests/test_restart_plan_contract.py`. Source inspection checked mutable title selection, both launch branches, atomic replacement, reload and return-to-supervisor behavior. The existing public-package naming issue already recorded in Round 2 is outside this revision's scope.

**Commands:** `git status --short`; `git log -5 --oneline`; `git show --stat 7bbc0c11`; `git diff 8c536da8 HEAD -- P`; capture-commit `git show`/`git rev-parse` and in-memory SHA-256 checks; full document reads; `sed`/`rg` source and contract checks; the in-memory Python assertion pass; fidelity and validation commands in the matrix. No files were written, processes signalled, messages sent or remote state changed. No pytest suite or live restart ran.

### R4 Receipt Payload Projection

`````json
{"findings":[{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"incorrect-behavior"},"disposition":"fixed","finding_key":"round1:F-2","fingerprint":"fp:restart-session:uuid-unpinned","id":"F-2","reachable_behavior":"The supervisor can resume another same-title transcript or create a fresh conversation before verification fails.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-dropped"},"disposition":"fixed","finding_key":"round1:JA-2","fingerprint":"fp:restart-session:feedback-excluded","id":"JA-2","reachable_behavior":"Completing the plan leaves requested follow-up feedback and issue delivery unaccounted for.","severity":"major","submitted_severity":"major"},{"cluster":{"invariant_id":null,"spec_version":"restart-session-plan@2737748b","subsystem":"session-restart","violation_kind":"ask-drift-narrowed"},"disposition":"fixed","finding_key":"round1:JA-3","fingerprint":"fp:restart-session:observer-omitted","id":"JA-3","reachable_behavior":"A restart reports success while requested peer visibility can remain broken.","severity":"major","submitted_severity":"major"}],"major_nonblocking_justifications":{},"new_by_severity":{"critical":0,"major":0,"minor":0},"scope":{"backlog":["round1:F-2","round1:JA-2","round1:JA-3"],"diff_since":"sha256:bf41ec9c54129e10a069565e468702b4046ca89e558bea023bf0b5dbe1b252fb","diff_summary":{"changed":true,"from":"sha256:bf41ec9c54129e10a069565e468702b4046ca89e558bea023bf0b5dbe1b252fb","to":"sha256:d2fb461881b4268c963dafb0d170131d464169f35ef0b0e7562b77297795e0a4"},"kind":"verify"},"source_event_id":"round-4-audit"}
`````
