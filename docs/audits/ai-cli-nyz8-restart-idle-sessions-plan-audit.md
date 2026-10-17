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
| Stage cursor | `doc-audit active; round 1` |
| Promotion | `blocked` |
<!-- doc:region name="loop_receipt" kind="generated" -->
## Status Summary

**Loop state:** active — NOT promotion ready

| Loop | Round | Target digest | New CRITICAL | New MAJOR | New MINOR | Open blocking | Scope | Stable |
|---|---:|---|---:|---:|---:|---|---|---|
| doc-audit | 1 | sha256:a7b78d48f05be58cca6c766510999a1740d03f213d1be51509e4f4b69f62ed43 | 0 | 6 | 1 | round1:DV-1, round1:F-1, round1:F-2, round1:JA-1, round1:JA-2, round1:JA-3 | full | no |
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
